import os
import json
import time
import logging
import threading
import queue
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Setup log handlers (file + console)
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

# Load env vars and product watchlist from config.json
class ConfigManager:
    @staticmethod
    def get_token():
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
            logger.error("config.json não encontrado!")
            return []

# Observer pattern for multi-channel alerts
@dataclass
class Notification:
    message: str
    priority: int = 1  # 0=critical, 1=info

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
            requests.post(url, data={"chat_id": self.chat_id, "text": notification.message})
            logger.info(f"[TELEGRAM] Enviado: {notification.message[:30]}...")
        except Exception as e:
            logger.error(f"[TELEGRAM] Falha: {e}")

class AlertSystem:
    def __init__(self):
        self.notifiers = []
        self._queue = queue.PriorityQueue()
        self._running = True
        self._worker = threading.Thread(target=self._process_queue, daemon=True)
        self._worker.start()
        # 30min cooldown per product URL to prevent notification spam
        self._last_alert = {} 

    def add_notifier(self, notifier: BaseNotifier):
        self.notifiers.append(notifier)

    def notify(self, message, is_critical=False, product_url=None):
        # Skip if alert was sent for this product in last 30min
        if product_url:
            last_time = self._last_alert.get(product_url, 0)
            if time.time() - last_time < 1800:
                logger.info(f"Alert suppressed (cooldown): {message[:20]}")
                return
            self._last_alert[product_url] = time.time()

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

# Selenium scraper with site-specific selectors
class PriceExtractor:
    def __init__(self):
        self.options = Options()
        self.options.add_argument("--headless")
        self.options.add_argument("--no-sandbox")
        self.options.add_argument("--disable-dev-shm-usage")
        self.options.add_argument("--disable-gpu")
        self.options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36")
        self.driver = None

    def start_driver(self):
        if not self.driver:
            self.driver = webdriver.Chrome(options=self.options)

    def stop_driver(self):
        if self.driver:
            self.driver.quit()
            self.driver = None
            
    def get_price(self, url):
        self.start_driver()
        
        # Guard: prevent crash if driver init failed
        if not self.driver:
            logger.error("Driver failed to initialize")
            return None

        try:
            logger.info(f"Fetching: {url}")
            self.driver.get(url)
            # Wait for JS rendering
            time.sleep(3) 

            texto_preco = ""
            
            # Site-specific selectors (sites change their DOM frequently)
            if "kabum" in url:
                try:
                    el = self.driver.find_element(By.XPATH, "//h4[contains(@class, 'finalPrice')]")
                    texto_preco = el.text
                except:
                    try:
                        # Fallback selector
                        el = self.driver.find_element(By.ID, "blocoValores")
                        texto_preco = el.text
                    except: pass
            
            elif "terabyteshop" in url:
                try:
                    texto_preco = self.driver.find_element(By.ID, "valVista").text
                except: pass

            elif "pichau" in url:
                try:
                    # Pichau class names are unstable; use text selector as anchor
                    el = self.driver.find_element(By.XPATH, "//*[contains(text(), 'à vista')]/preceding-sibling::div")
                    texto_preco = el.text
                except: pass

            # Parse Brazilian price format: R$ 1.200,00 -> 1200.0
            if texto_preco:
                match = re.search(r'[\d\.]+, \d{2}|[\d\.]+\d{2}', texto_preco)
                if match:
                    valor_limpo = match.group(0).replace('R$', '').replace('.', '').replace(',', '.')
                    return float(valor_limpo)
            
            logger.warning(f"Price not found in: {texto_preco[:20]}")
            return None

        except Exception as e:
            logger.error(f"Error reading page: {e}")
            return None

def main():
    logger.info("Starting SpellHunter Bot...")
    
    config = ConfigManager()
    alerts = AlertSystem()
    extractor = PriceExtractor()
    
    alerts.add_notifier(ConsoleNotifier())
    if config.get_token():
        alerts.add_notifier(TelegramNotifier(config.get_token(), config.get_chat_id()))
        alerts.notify("🤖 Bot started, monitoring prices...", is_critical=False)
    
    try:
        while True:
            products = config.load_products()
            logger.info(f"Checking {len(products)} products...")
            
            for item in products:
                url = item.get('url')
                target = item.get('target_price', 0)
                name = item.get('name', 'Produto Desconhecido')
                
                current_price = extractor.get_price(url)
                
                if current_price:
                    logger.info(f"[{name}] Price: R$ {current_price:.2f} | Target: R$ {target:.2f}")
                    
                    # Alert if price dropped to or below target
                    if 0 < current_price <= target:
                        msg = f"🚨 PRICE ALERT!\n\n📦 {name}\n💰 From: R$ {target}\n📉 To: R$ {current_price:.2f}\n🔗 {url}"
                        alerts.notify(msg, is_critical=True, product_url=url)
                else:
                    logger.warning(f"Failed to fetch price for {name}")

            logger.info("Waiting 60s...")
            time.sleep(60)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
        extractor.stop_driver()

if __name__ == "__main__":
    main()