"""Build Figure 6.1 for the dissertation from the transfer-forecasting test predictions."""
from pathlib import Path
from datetime import datetime
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

BASE = Path(__file__).resolve().parents[2]
PRED = BASE / "results" / "forecasting" / "predictions_h24.csv"
FIG = BASE.parent.parent / "Dissertation_Document" / "figures" / "fig6_transfer_forecast.pdf"

df = pd.read_csv(PRED, parse_dates=["timestamp_utc"]).sort_values("timestamp_utc")
df = df.set_index("timestamp_utc")

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7))
fig.suptitle("Kazakhstan Hourly Electricity Demand — EU-to-KZ Transfer Forecast (24 h horizon)",
             fontsize=12, fontweight="bold")

# (a) full test period, daily mean for legibility
daily = df.resample("D").mean(numeric_only=True)
ax1.plot(daily.index, daily["y_true"], color="#444444", lw=1.1, label="Actual")
ax1.plot(daily.index, daily["pred_catboost_transfer"], color="#d62728", lw=1.4,
         label="CatBoost (transfer)")
ax1.plot(daily.index, daily["pred_seasonal_naive"], color="#1f77b4", lw=1.1, ls="--",
         label="Seasonal naive")
ax1.set_title("(a) Full Kazakhstan test period (daily mean of the 24-hour-ahead forecast)",
              fontsize=10)
ax1.set_ylabel("Demand (MW)")
ax1.legend(fontsize=8, loc="best")
ax1.grid(alpha=0.3)
ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))

# (b) zoom: a representative 14-day window with conformal interval
win = df.iloc[480:480 + 24 * 14]
ax2.plot(win.index, win["y_true"], color="#444444", lw=1.2, label="Actual")
ax2.plot(win.index, win["pred_catboost_transfer"], color="#d62728", lw=1.4,
         label="CatBoost (transfer)")
ax2.fill_between(win.index, win["lo_catboost_transfer"], win["hi_catboost_transfer"],
                 color="#d62728", alpha=0.15, label="90% conformal interval")
ax2.plot(win.index, win["pred_seasonal_naive"], color="#1f77b4", lw=1.0, ls="--",
         label="Seasonal naive")
ax2.set_title("(b) Detail: representative 14-day window with 90% conformal prediction interval",
              fontsize=10)
ax2.set_ylabel("Demand (MW)")
ax2.set_xlabel("Test period (hourly)")
ax2.legend(fontsize=8, loc="best")
ax2.grid(alpha=0.3)
ax2.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))

fig.tight_layout(rect=[0, 0, 1, 0.96])
FIG.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(FIG, bbox_inches="tight")
plt.close(fig)
print(f"saved -> {FIG}")
