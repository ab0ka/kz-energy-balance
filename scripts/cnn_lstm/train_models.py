#!/usr/bin/env python3
"""
Template / skeleton script for training and evaluating multiple forecasting
models on ERA5-derived renewable-energy capacity factor data.

Models implemented
------------------
1. ARIMA           – classical statistical baseline
2. XGBoost         – gradient-boosted trees (Optuna-tuned)
3. LightGBM        – gradient-boosted trees (Optuna-tuned)
4. LSTM            – PyTorch recurrent neural network
5. CNN-LSTM        – PyTorch hybrid convolutional-recurrent network

Usage
-----
    python train_models.py --target cf_wind --location astana_wind
    python train_models.py --target cf_solar --location almaty_solar --epochs 100

NOTE: Adjust DATA_PATH and other constants in the "Configuration" section
before running.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pickle
import sys
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.preprocessing import StandardScaler

# ---------------------------------------------------------------------------
# >>>  CONFIGURE THESE PATHS  <<<
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Path to the processed CSV produced by download_era5.py --process
# Each row is one hour; must contain a 'time' column and the target column.
DATA_PATH = PROJECT_ROOT / "data" / "processed"  # directory with per-location CSVs

# Where to store trained artefacts and results
RESULTS_DIR = PROJECT_ROOT / "results"
MODELS_DIR = PROJECT_ROOT / "models"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("train_models")

# ---------------------------------------------------------------------------
# 1. DATA LOADING & FEATURE ENGINEERING
# ---------------------------------------------------------------------------

def load_data(location: str, target: str) -> pd.DataFrame:
    """
    Load the processed CSV for *location*, parse timestamps, sort by time,
    and verify that *target* column exists.
    """
    csv_path = DATA_PATH / f"{location}.csv"
    if not csv_path.exists():
        sys.exit(f"ERROR: data file not found: {csv_path}")

    log.info("Loading data from %s", csv_path)
    df = pd.read_csv(csv_path, parse_dates=["time"])
    df = df.sort_values("time").reset_index(drop=True)

    if target not in df.columns:
        sys.exit(
            f"ERROR: target column '{target}' not in data. "
            f"Available columns: {list(df.columns)}"
        )

    log.info("Loaded %d rows, %d columns.", len(df), len(df.columns))
    return df


def engineer_features(df: pd.DataFrame, target: str) -> pd.DataFrame:
    """
    Create calendar / lag / rolling features suitable for tree and
    neural-network models.

    Returns a DataFrame with no NaN rows (rows with incomplete lags are
    dropped).
    """
    df = df.copy()

    # ---- Calendar features ----
    df["hour"] = df["time"].dt.hour
    df["day_of_year"] = df["time"].dt.dayofyear
    df["month"] = df["time"].dt.month
    df["weekday"] = df["time"].dt.weekday
    # Cyclical encoding (sin/cos) so hour 23 is close to hour 0
    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24)
    df["doy_sin"] = np.sin(2 * np.pi * df["day_of_year"] / 365.25)
    df["doy_cos"] = np.cos(2 * np.pi * df["day_of_year"] / 365.25)

    # ---- Lag features ----
    for lag in [1, 2, 3, 6, 12, 24, 48, 168]:  # hours
        df[f"{target}_lag{lag}"] = df[target].shift(lag)

    # ---- Rolling statistics ----
    for window in [6, 24, 168]:  # 6h, 1d, 1wk
        df[f"{target}_rmean{window}"] = (
            df[target].rolling(window, min_periods=1).mean()
        )
        df[f"{target}_rstd{window}"] = (
            df[target].rolling(window, min_periods=1).std()
        )

    # Drop rows that have NaN due to lagging
    df = df.dropna().reset_index(drop=True)
    log.info("After feature engineering: %d rows, %d columns.", len(df), len(df.columns))
    return df


# ---------------------------------------------------------------------------
# 2. TRAIN / VALIDATION / TEST SPLIT
# ---------------------------------------------------------------------------

def temporal_split(
    df: pd.DataFrame,
    train_frac: float = 0.7,
    val_frac: float = 0.15,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Chronological split (no shuffling) to prevent data leakage.

    Default: 70 % train, 15 % validation, 15 % test.
    """
    n = len(df)
    train_end = int(n * train_frac)
    val_end = int(n * (train_frac + val_frac))

    train = df.iloc[:train_end].copy()
    val = df.iloc[train_end:val_end].copy()
    test = df.iloc[val_end:].copy()

    log.info(
        "Split sizes -> train: %d  val: %d  test: %d",
        len(train), len(val), len(test),
    )
    return train, val, test


def get_feature_target(
    df: pd.DataFrame,
    target: str,
    exclude: List[str] | None = None,
) -> Tuple[pd.DataFrame, pd.Series]:
    """Separate feature matrix X and target vector y."""
    exclude = exclude or []
    drop_cols = [target, "time", "location", "resource"] + exclude
    feature_cols = [c for c in df.columns if c not in drop_cols]
    X = df[feature_cols]
    y = df[target]
    return X, y


# ---------------------------------------------------------------------------
# 3. EVALUATION UTILITIES
# ---------------------------------------------------------------------------

def evaluate(y_true: np.ndarray, y_pred: np.ndarray, label: str) -> Dict[str, float]:
    """Compute MAE, RMSE, R2 and log them."""
    mae = mean_absolute_error(y_true, y_pred)
    rmse = np.sqrt(mean_squared_error(y_true, y_pred))
    r2 = r2_score(y_true, y_pred)
    log.info("  %-12s  MAE=%.4f  RMSE=%.4f  R2=%.4f", label, mae, rmse, r2)
    return {"model": label, "MAE": mae, "RMSE": rmse, "R2": r2}


# ---------------------------------------------------------------------------
# 4. MODEL DEFINITIONS
# ---------------------------------------------------------------------------

# ---- 4a. ARIMA baseline ---------------------------------------------------

def train_arima(
    train_y: pd.Series,
    test_y: pd.Series,
    order: Tuple[int, int, int] = (2, 1, 2),
    seasonal_order: Tuple[int, int, int, int] = (1, 0, 1, 24),
) -> Dict[str, float]:
    """
    Fit a SARIMAX model on the training target series and produce
    one-step-ahead forecasts on the test set.
    """
    from statsmodels.tsa.statespace.sarimax import SARIMAX

    log.info("Training ARIMA%s x %s ...", order, seasonal_order)
    warnings.filterwarnings("ignore", module="statsmodels")

    model = SARIMAX(
        train_y.values,
        order=order,
        seasonal_order=seasonal_order,
        enforce_stationarity=False,
        enforce_invertibility=False,
    )
    result = model.fit(disp=False, maxiter=200)

    # Forecast over the test period
    preds = result.forecast(steps=len(test_y))
    return evaluate(test_y.values, preds, "ARIMA")


# ---- 4b. XGBoost with Optuna ---------------------------------------------

def train_xgboost(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_val: pd.DataFrame,
    y_val: pd.Series,
    X_test: pd.DataFrame,
    y_test: pd.Series,
    n_trials: int = 50,
) -> Dict[str, float]:
    """
    Tune XGBoost hyperparameters with Optuna, refit on train+val with the
    best params, and evaluate on test.
    """
    import optuna
    import xgboost as xgb

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def objective(trial: optuna.Trial) -> float:
        params = {
            "n_estimators": trial.suggest_int("n_estimators", 200, 2000, step=100),
            "max_depth": trial.suggest_int("max_depth", 3, 12),
            "learning_rate": trial.suggest_float("learning_rate", 0.005, 0.3, log=True),
            "subsample": trial.suggest_float("subsample", 0.5, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.3, 1.0),
            "min_child_weight": trial.suggest_int("min_child_weight", 1, 20),
            "reg_alpha": trial.suggest_float("reg_alpha", 1e-8, 10.0, log=True),
            "reg_lambda": trial.suggest_float("reg_lambda", 1e-8, 10.0, log=True),
            "gamma": trial.suggest_float("gamma", 1e-8, 5.0, log=True),
        }
        model = xgb.XGBRegressor(
            **params,
            tree_method="hist",
            random_state=42,
            early_stopping_rounds=50,
            verbosity=0,
        )
        model.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            verbose=False,
        )
        preds = model.predict(X_val)
        return mean_squared_error(y_val, preds, squared=False)

    log.info("Tuning XGBoost (%d trials) ...", n_trials)
    study = optuna.create_study(direction="minimize")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)
    best = study.best_params
    log.info("  Best params: %s", best)

    # Refit on train + val
    X_fit = pd.concat([X_train, X_val])
    y_fit = pd.concat([y_train, y_val])
    final_model = xgb.XGBRegressor(**best, tree_method="hist", random_state=42, verbosity=0)
    final_model.fit(X_fit, y_fit)

    # Save model
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    final_model.save_model(str(MODELS_DIR / "xgboost_best.json"))

    preds = final_model.predict(X_test)
    return evaluate(y_test.values, preds, "XGBoost")


# ---- 4c. LightGBM with Optuna -------------------------------------------

def train_lightgbm(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_val: pd.DataFrame,
    y_val: pd.Series,
    X_test: pd.DataFrame,
    y_test: pd.Series,
    n_trials: int = 50,
) -> Dict[str, float]:
    """
    Tune LightGBM hyperparameters with Optuna, refit on train+val,
    evaluate on test.
    """
    import optuna
    import lightgbm as lgb

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def objective(trial: optuna.Trial) -> float:
        params = {
            "n_estimators": trial.suggest_int("n_estimators", 200, 3000, step=100),
            "max_depth": trial.suggest_int("max_depth", 3, 15),
            "learning_rate": trial.suggest_float("learning_rate", 0.005, 0.3, log=True),
            "num_leaves": trial.suggest_int("num_leaves", 20, 300),
            "subsample": trial.suggest_float("subsample", 0.5, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.3, 1.0),
            "min_child_samples": trial.suggest_int("min_child_samples", 5, 100),
            "reg_alpha": trial.suggest_float("reg_alpha", 1e-8, 10.0, log=True),
            "reg_lambda": trial.suggest_float("reg_lambda", 1e-8, 10.0, log=True),
        }
        model = lgb.LGBMRegressor(**params, random_state=42, verbosity=-1)
        model.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            callbacks=[lgb.early_stopping(50, verbose=False)],
        )
        preds = model.predict(X_val)
        return mean_squared_error(y_val, preds, squared=False)

    log.info("Tuning LightGBM (%d trials) ...", n_trials)
    study = optuna.create_study(direction="minimize")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)
    best = study.best_params
    log.info("  Best params: %s", best)

    X_fit = pd.concat([X_train, X_val])
    y_fit = pd.concat([y_train, y_val])
    final_model = lgb.LGBMRegressor(**best, random_state=42, verbosity=-1)
    final_model.fit(X_fit, y_fit)

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with open(MODELS_DIR / "lightgbm_best.pkl", "wb") as f:
        pickle.dump(final_model, f)

    preds = final_model.predict(X_test)
    return evaluate(y_test.values, preds, "LightGBM")


# ---- 4d. LSTM (PyTorch) --------------------------------------------------

def _build_sequences(
    X: np.ndarray, y: np.ndarray, seq_len: int
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Convert flat feature matrix + target vector into overlapping
    sequences of length *seq_len* for recurrent models.

    Returns
    -------
    X_seq : (N - seq_len, seq_len, n_features)
    y_seq : (N - seq_len,)
    """
    Xs, ys = [], []
    for i in range(seq_len, len(X)):
        Xs.append(X[i - seq_len : i])
        ys.append(y[i])
    return np.array(Xs, dtype=np.float32), np.array(ys, dtype=np.float32)


def train_lstm(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    seq_len: int = 24,
    hidden_size: int = 128,
    num_layers: int = 2,
    dropout: float = 0.2,
    lr: float = 1e-3,
    batch_size: int = 256,
    epochs: int = 50,
    patience: int = 10,
) -> Dict[str, float]:
    """
    Train a stacked LSTM regression model in PyTorch with early stopping.
    """
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log.info("Training LSTM on %s  (seq_len=%d, hidden=%d, layers=%d)",
             device, seq_len, hidden_size, num_layers)

    # Build sequences
    X_tr_seq, y_tr_seq = _build_sequences(X_train, y_train, seq_len)
    X_va_seq, y_va_seq = _build_sequences(X_val, y_val, seq_len)
    X_te_seq, y_te_seq = _build_sequences(X_test, y_test, seq_len)

    n_features = X_tr_seq.shape[2]

    # DataLoaders
    train_loader = DataLoader(
        TensorDataset(
            torch.from_numpy(X_tr_seq),
            torch.from_numpy(y_tr_seq),
        ),
        batch_size=batch_size, shuffle=True,
    )
    val_loader = DataLoader(
        TensorDataset(
            torch.from_numpy(X_va_seq),
            torch.from_numpy(y_va_seq),
        ),
        batch_size=batch_size,
    )

    # ---- Model definition ----
    class LSTMModel(nn.Module):
        def __init__(self):
            super().__init__()
            self.lstm = nn.LSTM(
                input_size=n_features,
                hidden_size=hidden_size,
                num_layers=num_layers,
                dropout=dropout if num_layers > 1 else 0.0,
                batch_first=True,
            )
            self.dropout = nn.Dropout(dropout)
            self.fc = nn.Linear(hidden_size, 1)

        def forward(self, x):
            # x: (batch, seq_len, features)
            out, _ = self.lstm(x)
            out = out[:, -1, :]          # take last time step
            out = self.dropout(out)
            return self.fc(out).squeeze(-1)

    model = LSTMModel().to(device)
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, patience=5, factor=0.5
    )

    # ---- Training loop with early stopping ----
    best_val_loss = float("inf")
    best_state = None
    wait = 0

    for epoch in range(1, epochs + 1):
        model.train()
        train_loss = 0.0
        for xb, yb in train_loader:
            xb, yb = xb.to(device), yb.to(device)
            optimizer.zero_grad()
            pred = model(xb)
            loss = criterion(pred, yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            train_loss += loss.item() * len(xb)
        train_loss /= len(X_tr_seq)

        # Validation
        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for xb, yb in val_loader:
                xb, yb = xb.to(device), yb.to(device)
                pred = model(xb)
                val_loss += criterion(pred, yb).item() * len(xb)
        val_loss /= len(X_va_seq)
        scheduler.step(val_loss)

        if epoch % 5 == 0 or epoch == 1:
            log.info("  Epoch %3d/%d  train_loss=%.5f  val_loss=%.5f",
                     epoch, epochs, train_loss, val_loss)

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            wait = 0
        else:
            wait += 1
            if wait >= patience:
                log.info("  Early stopping at epoch %d", epoch)
                break

    # Restore best weights
    model.load_state_dict(best_state)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    torch.save(best_state, MODELS_DIR / "lstm_best.pt")

    # Test evaluation
    model.eval()
    with torch.no_grad():
        X_te_tensor = torch.from_numpy(X_te_seq).to(device)
        preds = model(X_te_tensor).cpu().numpy()

    return evaluate(y_te_seq, preds, "LSTM")


# ---- 4e. CNN-LSTM (PyTorch) -----------------------------------------------

def train_cnn_lstm(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    seq_len: int = 48,
    cnn_filters: int = 64,
    kernel_size: int = 3,
    lstm_hidden: int = 128,
    lstm_layers: int = 1,
    dropout: float = 0.3,
    lr: float = 1e-3,
    batch_size: int = 256,
    epochs: int = 50,
    patience: int = 10,
) -> Dict[str, float]:
    """
    Hybrid CNN-LSTM: 1-D convolution extracts local temporal patterns,
    followed by an LSTM to capture longer-range dependencies.
    """
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log.info("Training CNN-LSTM on %s  (seq=%d, filters=%d, kernel=%d, lstm_h=%d)",
             device, seq_len, cnn_filters, kernel_size, lstm_hidden)

    X_tr_seq, y_tr_seq = _build_sequences(X_train, y_train, seq_len)
    X_va_seq, y_va_seq = _build_sequences(X_val, y_val, seq_len)
    X_te_seq, y_te_seq = _build_sequences(X_test, y_test, seq_len)

    n_features = X_tr_seq.shape[2]

    train_loader = DataLoader(
        TensorDataset(torch.from_numpy(X_tr_seq), torch.from_numpy(y_tr_seq)),
        batch_size=batch_size, shuffle=True,
    )
    val_loader = DataLoader(
        TensorDataset(torch.from_numpy(X_va_seq), torch.from_numpy(y_va_seq)),
        batch_size=batch_size,
    )

    class CNNLSTMModel(nn.Module):
        """
        Architecture
        ------------
        Conv1D -> BatchNorm -> ReLU -> MaxPool
        Conv1D -> BatchNorm -> ReLU -> MaxPool
        LSTM (on the pooled sequence)
        Fully-connected head
        """
        def __init__(self):
            super().__init__()
            self.conv1 = nn.Conv1d(n_features, cnn_filters, kernel_size, padding="same")
            self.bn1 = nn.BatchNorm1d(cnn_filters)
            self.conv2 = nn.Conv1d(cnn_filters, cnn_filters * 2, kernel_size, padding="same")
            self.bn2 = nn.BatchNorm1d(cnn_filters * 2)
            self.pool = nn.MaxPool1d(2)
            self.lstm = nn.LSTM(
                input_size=cnn_filters * 2,
                hidden_size=lstm_hidden,
                num_layers=lstm_layers,
                batch_first=True,
                dropout=dropout if lstm_layers > 1 else 0.0,
            )
            self.dropout = nn.Dropout(dropout)
            self.fc1 = nn.Linear(lstm_hidden, 64)
            self.fc2 = nn.Linear(64, 1)
            self.relu = nn.ReLU()

        def forward(self, x):
            # x: (batch, seq_len, features)  ->  Conv1d needs (batch, features, seq_len)
            x = x.permute(0, 2, 1)
            x = self.relu(self.bn1(self.conv1(x)))
            x = self.pool(x)
            x = self.relu(self.bn2(self.conv2(x)))
            x = self.pool(x)
            # Back to (batch, seq, channels) for LSTM
            x = x.permute(0, 2, 1)
            out, _ = self.lstm(x)
            out = out[:, -1, :]
            out = self.dropout(out)
            out = self.relu(self.fc1(out))
            return self.fc2(out).squeeze(-1)

    model = CNNLSTMModel().to(device)
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)

    best_val_loss = float("inf")
    best_state = None
    wait = 0

    for epoch in range(1, epochs + 1):
        model.train()
        train_loss = 0.0
        for xb, yb in train_loader:
            xb, yb = xb.to(device), yb.to(device)
            optimizer.zero_grad()
            pred = model(xb)
            loss = criterion(pred, yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            train_loss += loss.item() * len(xb)
        train_loss /= len(X_tr_seq)

        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for xb, yb in val_loader:
                xb, yb = xb.to(device), yb.to(device)
                val_loss += criterion(model(xb), yb).item() * len(xb)
        val_loss /= len(X_va_seq)
        scheduler.step(val_loss)

        if epoch % 5 == 0 or epoch == 1:
            log.info("  Epoch %3d/%d  train_loss=%.5f  val_loss=%.5f",
                     epoch, epochs, train_loss, val_loss)

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            wait = 0
        else:
            wait += 1
            if wait >= patience:
                log.info("  Early stopping at epoch %d", epoch)
                break

    model.load_state_dict(best_state)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    torch.save(best_state, MODELS_DIR / "cnn_lstm_best.pt")

    model.eval()
    with torch.no_grad():
        preds = model(torch.from_numpy(X_te_seq).to(device)).cpu().numpy()

    return evaluate(y_te_seq, preds, "CNN-LSTM")


# ---------------------------------------------------------------------------
# 5. SHAP ANALYSIS (for tree-based models)
# ---------------------------------------------------------------------------

def run_shap_analysis(
    model_path: Path,
    X_test: pd.DataFrame,
    model_type: str = "xgboost",
):
    """
    Compute and save SHAP values + summary plot for a tree model.
    """
    import shap
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    log.info("Running SHAP analysis for %s ...", model_type)

    if model_type == "xgboost":
        import xgboost as xgb
        model = xgb.XGBRegressor()
        model.load_model(str(model_path))
        explainer = shap.TreeExplainer(model)
    elif model_type == "lightgbm":
        with open(model_path, "rb") as f:
            model = pickle.load(f)
        explainer = shap.TreeExplainer(model)
    else:
        log.warning("SHAP analysis not implemented for %s", model_type)
        return

    # Use a subsample for speed if the test set is large
    sample = X_test.sample(n=min(2000, len(X_test)), random_state=42)
    shap_values = explainer.shap_values(sample)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Summary bar plot
    plt.figure(figsize=(10, 8))
    shap.summary_plot(shap_values, sample, plot_type="bar", show=False)
    plt.tight_layout()
    plt.savefig(RESULTS_DIR / f"shap_bar_{model_type}.png", dpi=150)
    plt.close()

    # Beeswarm plot
    plt.figure(figsize=(10, 8))
    shap.summary_plot(shap_values, sample, show=False)
    plt.tight_layout()
    plt.savefig(RESULTS_DIR / f"shap_beeswarm_{model_type}.png", dpi=150)
    plt.close()

    log.info("  SHAP plots saved to %s", RESULTS_DIR)


# ---------------------------------------------------------------------------
# 6. MAIN PIPELINE
# ---------------------------------------------------------------------------

def run_pipeline(args: argparse.Namespace):
    """Orchestrate the full training and evaluation pipeline."""

    # ---- Load & prepare data ----
    df = load_data(args.location, args.target)
    df = engineer_features(df, args.target)
    train_df, val_df, test_df = temporal_split(df)

    # Separate features / targets (for tree models)
    X_train, y_train = get_feature_target(train_df, args.target)
    X_val, y_val = get_feature_target(val_df, args.target)
    X_test, y_test = get_feature_target(test_df, args.target)

    # Scale features (for neural-network models)
    scaler = StandardScaler()
    X_train_sc = scaler.fit_transform(X_train.select_dtypes(include=[np.number]))
    X_val_sc = scaler.transform(X_val.select_dtypes(include=[np.number]))
    X_test_sc = scaler.transform(X_test.select_dtypes(include=[np.number]))

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with open(MODELS_DIR / "feature_scaler.pkl", "wb") as f:
        pickle.dump(scaler, f)

    results: List[Dict[str, float]] = []

    # ---- 1. ARIMA baseline ----
    if not args.skip_arima:
        try:
            res = train_arima(y_train, y_test)
            results.append(res)
        except Exception as exc:
            log.error("ARIMA failed: %s", exc, exc_info=True)

    # ---- 2. XGBoost ----
    try:
        res = train_xgboost(X_train, y_train, X_val, y_val, X_test, y_test,
                            n_trials=args.optuna_trials)
        results.append(res)
    except Exception as exc:
        log.error("XGBoost failed: %s", exc, exc_info=True)

    # ---- 3. LightGBM ----
    try:
        res = train_lightgbm(X_train, y_train, X_val, y_val, X_test, y_test,
                             n_trials=args.optuna_trials)
        results.append(res)
    except Exception as exc:
        log.error("LightGBM failed: %s", exc, exc_info=True)

    # ---- 4. LSTM ----
    try:
        res = train_lstm(
            X_train_sc, y_train.values,
            X_val_sc, y_val.values,
            X_test_sc, y_test.values,
            seq_len=args.seq_len,
            epochs=args.epochs,
        )
        results.append(res)
    except Exception as exc:
        log.error("LSTM failed: %s", exc, exc_info=True)

    # ---- 5. CNN-LSTM ----
    try:
        res = train_cnn_lstm(
            X_train_sc, y_train.values,
            X_val_sc, y_val.values,
            X_test_sc, y_test.values,
            seq_len=args.seq_len * 2,  # CNN-LSTM benefits from longer context
            epochs=args.epochs,
        )
        results.append(res)
    except Exception as exc:
        log.error("CNN-LSTM failed: %s", exc, exc_info=True)

    # ---- 6. SHAP analysis ----
    xgb_model = MODELS_DIR / "xgboost_best.json"
    lgb_model = MODELS_DIR / "lightgbm_best.pkl"
    if xgb_model.exists():
        try:
            run_shap_analysis(xgb_model, X_test, "xgboost")
        except Exception as exc:
            log.error("SHAP (XGBoost) failed: %s", exc)
    if lgb_model.exists():
        try:
            run_shap_analysis(lgb_model, X_test, "lightgbm")
        except Exception as exc:
            log.error("SHAP (LightGBM) failed: %s", exc)

    # ---- 7. Save comparison table ----
    if results:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        results_df = pd.DataFrame(results)
        results_path = RESULTS_DIR / f"model_comparison_{args.location}_{args.target}.csv"
        results_df.to_csv(results_path, index=False)
        log.info("\n%s", results_df.to_string(index=False))
        log.info("Results saved to %s", results_path)

        # Also save as JSON for programmatic access
        with open(results_path.with_suffix(".json"), "w") as f:
            json.dump(results, f, indent=2)

    log.info("Pipeline complete.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Train and evaluate forecasting models for Kazakhstan renewables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--location", type=str, default="astana_wind",
        choices=["astana_wind", "zhambyl_solar", "mangystau_wind", "almaty_solar"],
        help="Location label (must match a CSV filename in data/processed/).",
    )
    p.add_argument(
        "--target", type=str, default="cf_wind",
        help="Target column to forecast (e.g. cf_wind, cf_solar, ws100, ghi_wm2).",
    )
    p.add_argument("--epochs", type=int, default=50, help="Max epochs for neural nets.")
    p.add_argument("--seq-len", type=int, default=24, help="Sequence length for LSTM (hours).")
    p.add_argument("--optuna-trials", type=int, default=50, help="Number of Optuna trials.")
    p.add_argument("--skip-arima", action="store_true", help="Skip ARIMA (slow on long series).")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    run_pipeline(args)
