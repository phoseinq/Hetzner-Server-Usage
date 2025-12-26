import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
    HETZNER_API_TOKEN = os.getenv('HETZNER_API_TOKEN')
    ADMIN_ID = int(os.getenv('ADMIN_ID', 0))
    DEBUG_MODE = os.getenv('DEBUG_MODE', 'false').lower() == 'true'
    
    HETZNER_API_BASE = 'https://api.hetzner.cloud/v1'
    TRAFFIC_LIMIT_TB = 20
    TRAFFIC_LIMIT_BYTES = TRAFFIC_LIMIT_TB * 1024 ** 4
    
    WARNING_THRESHOLD = 0.75
    CRITICAL_THRESHOLD = 0.98
    
    DATA_FILE = 'server_data.csv'
    
    @classmethod
    def validate(cls):
        if not cls.TELEGRAM_TOKEN:
            raise ValueError("TELEGRAM_TOKEN is required in .env")
        if not cls.HETZNER_API_TOKEN:
            raise ValueError("HETZNER_API_TOKEN is required in .env")
        if not cls.ADMIN_ID:
            raise ValueError("ADMIN_ID is required in .env")

Config.validate()