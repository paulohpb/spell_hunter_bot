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

# --- CONFIGURAÇÃO DE LOGS ---
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

# --- GERENCIAMENTO DE CONFIGURAÇÃO ---
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

# --- SISTEMA DE NOTIFICAÇÃO ---
@dataclass
class Notification:
    message: str
    priority: int = 1

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
        self._last_alert = {} 

    def add_notifier(self, notifier: BaseNotifier):
        self.notifiers.append(notifier)

    def notify(self, message, is_critical=False, product_url=None):
        if product_url:
            last_time = self._last_alert.get(product_url, 0)
            if time.time() - last_time < 1800: # 30 min cooldown
                logger.info(f"Alert suprimido (cooldown): {message[:20]}")
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

# --- EXTRAÇÃO DE PREÇOS (ATUALIZADO) ---
class PriceExtractor:
    def __init__(self):
        self.options = Options()
        self.options.add_argument("--headless")
        self.options.add_argument("--no-sandbox")
        self.options.add_argument("--disable-dev-shm-usage")
        self.options.add_argument("--disable-gpu")
        self.options.add_argument("--window-size=1920,1080") # Evita layout mobile
        # Truque Anti-Bot: Desativa flag de automação
        self.options.add_argument("--disable-blink-features=AutomationControlled") 
        # User-Agent Moderno (Chrome 120)
        self.options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
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
        
        # Correção do erro Pylance (Segurança)
        if not self.driver:
            return None

        try:
            logger.info(f"Acessando: {url}")
            self.driver.get(url)
            time.sleep(5) # Espera um pouco mais para carregar scripts

            texto_preco = ""
            
            # --- ESTRATÉGIAS DE SELEÇÃO ---
            try:
                if "kabum" in url:
                    # Tenta pegar pelo bloco de preço final (Classes mudam, mas 'finalPrice' é comum)
                    elements = self.driver.find_elements(By.XPATH, "//*[contains(@class, 'finalPrice')]")
                    if elements:
                        texto_preco = elements[0].text
                    else:
                        # Estratégia Genérica: Procura qualquer H4 dentro do bloco de valores
                        el = self.driver.find_element(By.XPATH, "//*[@id='blocoValores']//h4")
                        texto_preco = el.text

                elif "terabyteshop" in url:
                    texto_preco = self.driver.find_element(By.ID, "valVista").text

                elif "pichau" in url:
                    # Pichau: Pega o elemento que tem o preço à vista
                    el = self.driver.find_element(By.XPATH, "//*[contains(text(), 'à vista')]/preceding-sibling::div")
                    texto_preco = el.text
            except Exception as e:
                # Se falhar o seletor específico, tenta achar qualquer "R$" grande na tela
                # Pega apenas a primeira linha do erro, sem o resto gigante
                erro_curto = str(e).split('\n')[0]
                logger.warning(f"Seletor padrão falhou, ativando busca genérica... (Erro: {erro_curto})")
                try:
                    # Pega o primeiro elemento H4 ou div que contenha "R$" e seja visível
                    generic = self.driver.find_element(By.XPATH, "//h4[contains(text(), 'R$')]")
                    texto_preco = generic.text
                except: pass

            # --- LIMPEZA E CONVERSÃO ---
            if texto_preco:
                # Regex poderoso para pegar "399,99" ou "1.200,00"
                match = re.search(r'[\d\.]+, \d{2}|[\d\.]+\d{2}', texto_preco)
                if match:
                    valor_limpo = match.group(0).replace('R$', '').replace('.', '').replace(',', '.')
                    return float(valor_limpo)
            
            # Debug: Se falhar, mostra o título da página para saber se fomos bloqueados
            logger.warning(f"Preço não encontrado. Título da página: {self.driver.title}")
            return None

        except Exception as e:
            logger.error(f"Erro crítico ao ler página: {e}")
            return None

# --- LOOP PRINCIPAL ---
def main():
    logger.info("--- INICIANDO SPELL HUNTER BOT V3 (FIX) ---")
    
    config = ConfigManager()
    alerts = AlertSystem()
    extractor = PriceExtractor()
    
    alerts.add_notifier(ConsoleNotifier())
    if config.get_token():
        alerts.add_notifier(TelegramNotifier(config.get_token(), config.get_chat_id()))
        alerts.notify("🤖 Bot Atualizado! Monitorando com nova engine...", is_critical=False)
    
    try:
        while True:
            products = config.load_products()
            logger.info(f"--- Varrendo {len(products)} produtos ---")
            
            for item in products:
                url = item.get('url')
                target = item.get('target_price', 0)
                name = item.get('name', 'Produto Desconhecido')
                
                current_price = extractor.get_price(url)
                
                if current_price:
                    logger.info(f"[{name}] Preço: R$ {current_price:.2f} | Alvo: R$ {target:.2f}")
                    
                    # AQUI ESTÁ A LÓGICA DE COMPARAÇÃO
                    if 0 < current_price <= target:
                        msg = f"🚨 PROMOÇÃO DETECTADA!\n\n📦 {name}\n💰 De: R$ {target}\n📉 Por: R$ {current_price:.2f}\n🔗 {url}"
                        alerts.notify(msg, is_critical=True, product_url=url)
                else:
                    logger.warning(f"Falha na leitura de {name}")

            logger.info("Dormindo 60 segundos...")
            time.sleep(60)

    except KeyboardInterrupt:
        logger.info("Parando Bot...")
        extractor.stop_driver()

if __name__ == "__main__":
    main()