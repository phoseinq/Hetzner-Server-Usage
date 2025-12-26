import json
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

class OverageTracker:
    def __init__(self, data_file='overage_history.json'):
        self.data_file = Path(data_file)
        self._ensure_file()
    
    def _ensure_file(self):
        if not self.data_file.exists():
            self.data_file.write_text('{}')
    
    def _load_data(self):
        try:
            return json.loads(self.data_file.read_text())
        except Exception as e:
            logger.error(f"Failed to load overage data: {e}")
            return {}
    
    def _save_data(self, data):
        try:
            self.data_file.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.error(f"Failed to save overage data: {e}")
    
    def record_monthly_overage(self, overage_cost):
        current_month = datetime.now().strftime('%Y-%m')
        data = self._load_data()
        
        data[current_month] = {
            'overage_cost': round(overage_cost, 2),
            'recorded_at': datetime.now().isoformat()
        }
        
        self._save_data(data)
        logger.info(f"Recorded overage for {current_month}: €{overage_cost:.2f}")
    
    def get_total_overage(self):
        data = self._load_data()
        total = sum(month_data.get('overage_cost', 0) for month_data in data.values())
        return round(total, 2)
    
    def get_monthly_breakdown(self):
        data = self._load_data()
        breakdown = []
        
        for month, month_data in sorted(data.items(), reverse=True):
            cost = month_data.get('overage_cost', 0)
            breakdown.append((month, cost))
        
        return breakdown
    
    def get_current_month_overage(self):
        current_month = datetime.now().strftime('%Y-%m')
        data = self._load_data()
        
        if current_month in data:
            return data[current_month].get('overage_cost', 0)
        return 0

overage_tracker = OverageTracker()
