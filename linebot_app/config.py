import os
from dotenv import load_dotenv

# 載入 .env 文件
load_dotenv()

class Config: # 必須是全大寫
    LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

    GDRIVE_JSON = os.getenv("GDRIVE_JSON", "linebot-380719-0621be0f3cad.json")
    GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL")
    GSPREADSHEET = os.getenv("GSPREADSHEET", "linebot_expense")
    GWORKSHEET = os.getenv("GWORKSHEET", "expense")
    THRESHOLD_AMOUNT = int(os.getenv("THRESHOLD_AMOUNT", "6000"))
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")