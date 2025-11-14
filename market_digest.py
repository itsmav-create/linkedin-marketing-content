import os
from datetime import datetime, timedelta, timezone

import yfinance as yf
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from openai import OpenAI

# ---------------------------------------------------------
# CONFIG – DAILY WATCHLIST (BROADER UNIVERSE)
# ---------------------------------------------------------

WATCHLIST = [
    # ASX ETFs
    "VAS.AX",
    "VGS.AX",
    "VGE.AX",
    "NDQ.AX",
    "IVV.AX",
    "STW.AX",

    # AU large caps
    "TLS.AX",
    "CBA.AX",
    "BHP.AX",
    "RIO.AX",
    "WES.AX",
    "CSL.AX",
    "XRO.AX",

    # US / global ETFs & sectors
    "QQQ",
    "SPY",
    "XLK",
    "XLF",
    "XLE",
    "XLU",
    "SMH",
    "VNQ",

    # US
