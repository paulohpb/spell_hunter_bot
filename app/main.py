import os
import json
import time
import logging
import threading
import queue
import requests
from abc import ABC, abstractmethod
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor

# Configure Logging
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logs/app.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- CONFIGURATION MANAGER ---
class ConfigManager:
    @staticmethod
    def get_token():
        # Env var priority over config file
        return os.getenv("TELEGRAM_TOKEN")

    @staticmethod
    def get_chat_id():
        return os.getenv("CHAT_ID")

    @staticmethod
    def load_products():
        try:
            with open("config.json", "r") as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error("config.json not found!")
            return []

# --- NOTIFICATION SYSTEM (Observer Pattern) ---
@dataclass
class Notification:
    message: str
    priority: int = 1  # 0=Critical, 1=Info

class BaseNotifier(ABC):
    @abstractmethod
    def send(self, notification: Notification):
        pass

class ConsoleNotifier(BaseNotifier):
    def send(self, notification: Notification):
        logger.info(f"[CONSOLE] {notification.message}")

class TelegramNotifier(BaseNotifier):
    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id
    
    def send(self, notification: Notification):
        if not self.token or not self.chat_id:
            return
        
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            # This sends the actual message to your phone
            requests.post(url, data={"chat_id": self.chat_id, "text": notification.message})
            logger.info(f"[TELEGRAM] Sent: {notification.message}")
        except Exception as e:
            logger.error(f"[TELEGRAM] Failed: {e}")

class AlertSystem:
    def __init__(self):
        self.notifiers = []
        self._queue = queue.PriorityQueue()
        self._running = True
        self._worker_thread = threading.Thread(target=self._process_queue, daemon=True)
        self._worker_thread.start()

    def add_notifier(self, notifier: BaseNotifier):
        self.notifiers.append(notifier)

    def notify(self, message, is_critical=False):
        priority = 0 if is_critical else 1
        self._queue.put((priority, Notification(message, priority)))

    def _process_queue(self):
        while self._running:
            try:
                priority, note = self._queue.get(timeout=1)
                with ThreadPoolExecutor(max_workers=3) as executor:
                    for notifier in self.notifiers:
                        executor.submit(notifier.send, note)
                self._queue.task_done()
            except queue.Empty:
                continue

# --- MAIN BOT LOGIC ---
def main():
    logger.info("Starting Spell Hunter Bot (Dockerized)...")
    
    # Initialize System
    config = ConfigManager()
    alerts = AlertSystem()
    
    # Add Observers
    alerts.add_notifier(ConsoleNotifier())
    if config.get_token():
        alerts.add_notifier(TelegramNotifier(config.get_token(), config.get_chat_id()))
    
    products = config.load_products()
    logger.info(f"Loaded {len(products)} products to monitor.")
    #testing
    # ... code above ...
    logger.info(f"Loaded {len(products)} products to monitor.")

    # FIRE A TEST MESSAGE
    alerts.notify("🚀 Bot Connected! Waiting for deals...", is_critical=False)

    # Main Loop
    while True:
    # ...
    # Main Loop (Mocked for safety in this script)
        try:
            while True:
                logger.info("Scanning prices...")
                # Real selenium logic would go here
                # For demonstration, we simulate a finding:
                # alerts.notify("Found RTX 5070!", is_critical=True)
                time.sleep(60)
        except KeyboardInterrupt:
            logger.info("Shutting down.")

if __name__ == "__main__":
    main()