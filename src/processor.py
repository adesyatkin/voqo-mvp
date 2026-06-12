"""
processor_unified.py - Унифицированный модуль обработки аудио с поддержкой больших файлов
Версия: автоматическая нарезка на чанки до 20 МБ, перекрытие 60 сек, сопоставление спикеров
Встроенная перерегистрация API-ключа pyannote при ошибках (с улучшенным логированием)
"""

import os
import sys
import logging
import subprocess
import tempfile
import shutil
import re
import json
import time
import requests
import hashlib
import threading
import math
import random
import string
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any
from datetime import datetime
import ffmpeg

# Импорт для Selenium (будет использоваться только при перерегистрации)
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait, Select
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.action_chains import ActionChains
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

# Импорт конфигурации pyannote и Telegram
try:
    from config_pyannote import PYANNOTE_CONFIG, TELEGRAM_CONFIG
    PYANNOTE_AVAILABLE = True
except ImportError as e:
    PYANNOTE_AVAILABLE = False
    print(f"⚠️  Ошибка импорта config_pyannote.py: {e}")
    print("⚠️  Диаризация через pyannote.ai недоступна.")

# Добавленный импорт SileroVAD
from src.audio_processing.vad import SileroVAD

# ============================================================================
# НАСТРОЙКА ЛОГГЕРА
# ============================================================================

def setup_logging():
    """Настройка логирования"""
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"processor_unified_{timestamp}.log"

    logger = logging.getLogger('AudioProcessorUnified')
    logger.setLevel(logging.DEBUG)  # Временно DEBUG для отладки

    if not logger.handlers:
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

        file_handler = logging.FileHandler(log_file, encoding='utf-8', mode='w')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    logger.info("=" * 70)
    logger.info("🚀 ЗАПУСК УНИФИЦИРОВАННОГО МОДУЛЯ PROCESSOR (С ПОДДЕРЖКОЙ БОЛЬШИХ ФАЙЛОВ)")
    logger.info(f"📝 Логирование в: {log_file}")
    logger.info(f"📊 Pyannote.ai доступен: {'✅ ДА' if PYANNOTE_AVAILABLE else '❌ НЕТ'}")
    logger.info("=" * 70)

    return logger

logger = setup_logging()


# ============================================================================
# ВСТРОЕННЫЕ КЛАССЫ ДЛЯ АВТОМАТИЧЕСКОЙ ПЕРЕРЕГИСТРАЦИИ API-КЛЮЧА
# ============================================================================

class TempMailClient:
    """Клиент для работы с временной почтой Mail.tm с улучшенным логированием"""

    def __init__(self):
        self.base_url = "https://api.mail.tm"
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/ld+json",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0"
        })
        self.token = None
        self.account_id = None
        self.email_address = None
        self.password = None

    def get_domains(self):
        """Получает список доступных доменов"""
        try:
            logger.info("🌐 Запрос списка доменов mail.tm...")
            response = self.session.get(f"{self.base_url}/domains", timeout=30)
            logger.debug(f"GET /domains status: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                domains = []
                for domain_info in data.get("hydra:member", []):
                    if domain_info.get("isActive"):
                        domain = domain_info.get("domain")
                        if domain:
                            domains.append(domain)
                logger.info(f"✅ Получено {len(domains)} доменов")
                return domains
            logger.error(f"❌ Ошибка получения доменов: {response.status_code}, ответ: {response.text[:200]}")
            return []
        except Exception as e:
            logger.error(f"❌ Ошибка получения доменов: {e}")
            return []

    def create_account(self):
        """Создает новый аккаунт и возвращает email"""
        domains = self.get_domains()
        if not domains:
            logger.error("Нет доступных доменов для создания почты")
            return None

        domain = domains[0]
        username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=12))
        self.email_address = f"{username}@{domain}"
        self.password = ''.join(random.choices(string.ascii_letters + string.digits, k=16))

        account_data = {"address": self.email_address, "password": self.password}
        logger.info(f"📧 Создаём аккаунт: {self.email_address}")

        try:
            response = self.session.post(f"{self.base_url}/accounts", json=account_data, timeout=30)
            logger.debug(f"POST /accounts status: {response.status_code}, ответ: {response.text[:200]}")
            if response.status_code in [200, 201]:
                self.account_id = response.json().get("id")
                logger.info("✅ Аккаунт создан")

                # Получаем токен
                logger.info("🔑 Получаем токен доступа...")
                token_response = requests.post(
                    f"{self.base_url}/token",
                    json={"address": self.email_address, "password": self.password},
                    timeout=30
                )
                logger.debug(f"POST /token status: {token_response.status_code}, ответ: {token_response.text[:200]}")
                if token_response.status_code == 200:
                    self.token = token_response.json().get("token")
                    if self.token:
                        self.session.headers.update({"Authorization": f"Bearer {self.token}"})
                        logger.info("✅ Токен получен и установлен в сессию")
                        return self.email_address
                logger.error("Не удалось получить токен")
                return None
            logger.error(f"❌ Ошибка создания аккаунта: {response.status_code}, {response.text}")
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка создания аккаунта: {e}")
            return None

    def get_messages(self):
        """Получает список сообщений"""
        try:
            response = self.session.get(f"{self.base_url}/messages", timeout=30)
            logger.debug(f"GET /messages status: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                messages = data.get("hydra:member", [])
                logger.info(f"📬 Получено {len(messages)} сообщений")
                # Выводим первые 3 сообщения для отладки
                for i, msg in enumerate(messages[:3]):
                    logger.debug(f"  Сообщение {i+1}: от {msg.get('from', {}).get('address')}, тема: {msg.get('subject')}")
                logger.debug(f"Полный ответ (первые 500 символов): {response.text[:500]}")
                return messages
            else:
                logger.warning(f"⚠️ Не удалось получить сообщения: {response.status_code}")
                logger.warning(f"Ответ: {response.text[:500]}")
                return []
        except Exception as e:
            logger.error(f"❌ Ошибка получения сообщений: {e}")
            return []

    def get_message_content(self, message_id):
        """Получает содержание сообщения"""
        try:
            response = self.session.get(f"{self.base_url}/messages/{message_id}", timeout=30)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка получения сообщения {message_id}: {e}")
            return None

    def wait_for_pyannote_code(self, timeout=300):
        """Ждет письмо от pyannote и извлекает код"""
        logger.info(f"⏳ Ожидание письма от pyannote (до {timeout} секунд)...")
        start_time = time.time()
        check_interval = 5

        while time.time() - start_time < timeout:
            messages = self.get_messages()
            if messages:
                logger.info(f"Найдено сообщений: {len(messages)}")
                for msg in messages:
                    try:
                        msg_id = msg.get('id')
                        if not msg_id:
                            continue
                        from_addr = msg.get('from', {}).get('address', '')
                        subject = msg.get('subject', '')
                        logger.debug(f"Проверяем письмо от {from_addr}, тема: {subject}")

                        if 'pyannote' in from_addr.lower():
                            logger.info(f"📧 Найдено письмо от pyannote: {subject}")

                            details = self.get_message_content(msg_id)
                            if not details:
                                continue

                            text = details.get('text', '')
                            html = details.get('html', '')

                            logger.debug(f"Текст письма (первые 500 символов):\n{text[:500]}")
                            if html:
                                logger.debug(f"HTML письма (первые 500 символов):\n{html[:500]}")

                            code = self._extract_code_from_text(text)
                            if not code and html:
                                code = self._extract_code_from_text(html)

                            if code:
                                logger.info(f"✅ Найден код: {code}")
                                return code
                            else:
                                logger.warning("⚠️ Не удалось извлечь код из письма")
                                # Показываем текст для ручного ввода
                                print("\n" + "="*60)
                                print("ПИСЬМО ОТ PYANNOTE ПОЛУЧЕНО, НО АВТОМАТИЧЕСКИЙ ПОИСК КОДА НЕ УДАЛСЯ")
                                print("="*60)
                                print("Текст письма (первые 500 символов):")
                                print("-"*60)
                                print(text[:500])
                                print("-"*60)
                                code = input("Введите код из письма (6 символов): ").strip()
                                if code and len(code) == 6:
                                    return code
                                else:
                                    print("❌ Неверный формат кода")
                                    return None
                    except Exception as e:
                        logger.error(f"Ошибка обработки письма: {e}")
                        continue
            else:
                logger.debug("Пока нет сообщений")

            time.sleep(check_interval)

        logger.error("❌ Таймаут ожидания кода")
        print("\n⚠️ Письмо не пришло за отведенное время.")
        code = input("Введите код из письма вручную (если письмо получено): ").strip()
        if code and len(code) == 6:
            return code
        return None

    def _extract_code_from_text(self, text):
        """Извлекает код из текста"""
        if not text:
            return None

        # Убираем HTML теги
        text = re.sub(r'<[^>]+>', ' ', text)

        # Ищем 6 символов (буквы и цифры)
        matches = re.findall(r'\b([a-zA-Z0-9]{6})\b', text)
        for match in matches:
            if match:
                return match

        # Ищем в формате: код: xxxxxx
        patterns = [
            r'code[: ]+([a-zA-Z0-9]{6})',
            r'код[: ]+([a-zA-Z0-9]{6})',
            r'Use the following code[: ]+([a-zA-Z0-9]{6})',
            r'Your code[: ]+([a-zA-Z0-9]{6})',
            r'Verification code[: ]+([a-zA-Z0-9]{6})',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)

        return None


class PyannoteAutoRegister:
    """Класс для автоматической регистрации на pyannote.ai и получения API-ключа"""

    def __init__(self, email, logger):
        self.email = email
        self.logger = logger
        self.driver = None
        self.wait = None
        self.api_key = None

    def _init_driver(self):
        """Инициализация браузера Chrome"""
        chrome_options = Options()
        chrome_options.add_argument("--start-maximized")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)

        self.driver = webdriver.Chrome(options=chrome_options)
        self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        self.wait = WebDriverWait(self.driver, 30)
        self.logger.info("✅ Браузер инициализирован")

    def _click_with_retry(self, element, description="", max_retries=3):
        """Попытка клика с несколькими методами"""
        methods = [
            ("Обычный клик", lambda: element.click()),
            ("JS клик", lambda: self.driver.execute_script("arguments[0].click();", element)),
            ("ActionChains клик", lambda: ActionChains(self.driver).move_to_element(element).click().perform()),
        ]

        for attempt in range(max_retries):
            for method_name, method in methods:
                try:
                    method()
                    if description:
                        self.logger.debug(f"{description} ({method_name}, попытка {attempt + 1})")
                    return True
                except Exception:
                    continue
            time.sleep(1)

        self.logger.warning(f"Не удалось кликнуть на {description} после {max_retries} попыток")
        return False

    def step_1_enter_email(self):
        """Шаг 1: Ввод email"""
        self.logger.info("🔓 Открываем страницу входа...")
        self.driver.get("https://dashboard.pyannote.ai/signin")
        time.sleep(2)

        try:
            self.logger.info(f"✍️ Вводим email: {self.email}")
            email_field = self.wait.until(EC.presence_of_element_located((By.ID, "email")))
            email_field.clear()
            email_field.send_keys(self.email)

            try:
                checkbox = self.driver.find_element(By.CSS_SELECTOR, "button[role='checkbox'][data-slot='checkbox']")
                self._click_with_retry(checkbox, "Галочка согласия")
            except Exception:
                pass

            submit_button = self.driver.find_element(By.XPATH, "//button[@type='submit' and contains(text(), 'Send login code')]")
            self._click_with_retry(submit_button, "Кнопка Send login code")
            self.logger.info("✅ Код отправлен на email")
            return True
        except Exception as e:
            self.logger.error(f"Ошибка ввода email: {e}")
            return False

    def step_2_enter_verification_code(self, code):
        """Шаг 2: Ввод кода подтверждения"""
        self.logger.info(f"🔑 Вводим код: {code}")
        if len(code) != 6:
            self.logger.error("Код должен быть 6 символов")
            return False

        try:
            code_field = self.wait.until(EC.presence_of_element_located((By.NAME, "token")))
            code_field.clear()
            code_field.send_keys(code)

            verify_button = self.driver.find_element(By.XPATH, "//button[@type='submit' and contains(text(), 'Verify and continue')]")
            self._click_with_retry(verify_button, "Кнопка Verify and continue")
            return True
        except Exception as e:
            self.logger.error(f"Ошибка ввода кода: {e}")
            return False

    def step_3_create_your_own_team(self):
        """Шаг 3: Выбор 'Create your own team'"""
        self.logger.info("👥 Выбираем 'Create your own team'...")
        time.sleep(3)
        try:
            create_team_link = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), 'Create your own team')]")))
            self._click_with_retry(create_team_link, "Выбрано 'Create your own team'")
            return True
        except Exception as e:
            self.logger.error(f"Ошибка выбора создания команды: {e}")
            return False

    def step_4_fill_team_info(self):
        """Шаг 4: Заполнение информации о команде"""
        self.logger.info("📋 Заполняем информацию о команде...")
        time.sleep(3)

        try:
            # Текстовые поля
            first_name = self.driver.find_element(By.ID, "firstName")
            first_name.clear()
            first_name.send_keys("Test")

            last_name = self.driver.find_element(By.ID, "lastName")
            last_name.clear()
            last_name.send_keys("User")

            company = self.driver.find_element(By.ID, "company")
            company.clear()
            company.send_keys("TestOrg")

            title = self.driver.find_element(By.ID, "title")
            title.clear()
            title.send_keys("Developer")

            # Продукт
            product_checkbox = self.driver.find_element(By.ID, "product-diarization")
            if not product_checkbox.is_selected():
                self._click_with_retry(product_checkbox, "Выбор продукта Diarization")

            # Описание использования
            usage_select = Select(self.driver.find_element(By.ID, "usage"))
            options = [opt for opt in usage_select.options if opt.get_attribute("value") and not opt.get_attribute("disabled")]
            if options:
                usage_select.select_by_index(1)

            # Объем
            volume_select = Select(self.driver.find_element(By.ID, "volume"))
            options = [opt for opt in volume_select.options if opt.get_attribute("value") and not opt.get_attribute("disabled")]
            if options:
                volume_select.select_by_index(1)

            # Use cases
            use_case_checkboxes = self.driver.find_elements(By.CSS_SELECTOR, "[id^='use-case-']")
            for i, cb in enumerate(use_case_checkboxes[:2]):
                self._click_with_retry(cb, f"Выбор use case #{i+1}")

            # Pipeline
            pipeline_select = Select(self.driver.find_element(By.ID, "pipeline"))
            options = [opt for opt in pipeline_select.options if opt.get_attribute("value") and not opt.get_attribute("disabled")]
            if options:
                pipeline_select.select_by_index(1)

            self.logger.info("✅ Форма заполнена")
            return True
        except Exception as e:
            self.logger.error(f"Ошибка заполнения формы: {e}")
            return False

    def step_5_create_team_and_continue(self):
        """Шаг 5: Нажатие кнопки создания команды"""
        self.logger.info("🚀 Создаём команду...")
        time.sleep(2)
        xpath_variants = [
            "//button[@type='submit' and contains(text(), 'Create team and continue')]",
            "//button[@type='submit' and contains(text(), 'Create team')]",
            "//button[contains(text(), 'Create team')]",
            "//button[contains(text(), 'Continue')]",
            "//button[contains(text(), 'Submit')]",
            "//button[@type='submit']",
        ]

        for xpath in xpath_variants:
            try:
                button = self.driver.find_element(By.XPATH, xpath)
                self._click_with_retry(button, "Кнопка создания команды")
                return True
            except Exception:
                continue
        return False

    def step_7_navigate_to_api_keys(self):
        """Шаг 7: Переход в раздел API Keys"""
        self.logger.info("🔧 Переход в раздел API Keys...")
        time.sleep(2)

        try:
            api_link = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), 'Create API key')]")))
            self._click_with_retry(api_link, "Ссылка Create API key")
            return True
        except Exception:
            pass

        try:
            api_link = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(@href, '/api-keys')]")))
            self._click_with_retry(api_link, "Ссылка на API Keys")
            return True
        except Exception:
            pass

        try:
            self.driver.find_element(By.XPATH, "//button[contains(text(), 'Create new key')]")
            return True
        except Exception:
            pass

        return False

    def step_8_create_new_api_key(self):
        """Шаг 8: Создание нового API ключа"""
        self.logger.info("🔑 Создаём API ключ...")
        time.sleep(2)

        try:
            create_btn = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Create new key')]")))
            self._click_with_retry(create_btn, "Кнопка Create new key")

            label_input = self.wait.until(EC.presence_of_element_located((By.ID, "label")))
            label_input.clear()
            label_input.send_keys("AutoKey")

            create_final = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(@class, 'bg-primary') and contains(text(), 'Create')]")))
            self._click_with_retry(create_final, "Кнопка Create в модалке")

            time.sleep(5)
            return True
        except Exception as e:
            self.logger.error(f"Ошибка создания API ключа: {e}")
            return False

    def step_9_copy_api_key(self):
        """Шаг 9: Копирование API ключа"""
        self.logger.info("📋 Копируем API ключ...")
        time.sleep(3)

        api_key = None

        # Поиск ключа в элементах code/pre
        try:
            key_elements = self.driver.find_elements(By.CSS_SELECTOR, "code, pre, [class*='mono'], [class*='font-mono']")
            for el in key_elements:
                text = el.text.strip()
                if len(text) > 20 and ' ' not in text and any(c.isalpha() for c in text) and any(c.isdigit() for c in text):
                    api_key = text
                    break
        except Exception:
            pass

        if not api_key:
            try:
                inputs = self.driver.find_elements(By.TAG_NAME, "input")
                for inp in inputs:
                    value = inp.get_attribute("value")
                    if value and len(value) > 20 and ' ' not in value:
                        api_key = value
                        break
            except Exception:
                pass

        if not api_key:
            try:
                body_text = self.driver.find_element(By.TAG_NAME, "body").text
                for line in body_text.split('\n'):
                    line = line.strip()
                    if len(line) > 20 and ' ' not in line and re.match(r'^[a-zA-Z0-9_\-]+$', line):
                        api_key = line
                        break
            except Exception:
                pass

        if api_key:
            self.api_key = api_key
            self.logger.info("✅ API ключ получен")
            return True
        else:
            self.logger.error("❌ Не удалось найти API ключ на странице")
            return False

    def run_registration(self, code):
        """Основной метод запуска регистрации"""
        steps = [
            ("Ввод email", self.step_1_enter_email),
            ("Ввод кода", lambda: self.step_2_enter_verification_code(code)),
            ("Создание своей команды", self.step_3_create_your_own_team),
            ("Заполнение информации", self.step_4_fill_team_info),
            ("Создание команды", self.step_5_create_team_and_continue),
            ("Переход к API Keys", self.step_7_navigate_to_api_keys),
            ("Создание API ключа", self.step_8_create_new_api_key),
            ("Копирование API ключа", self.step_9_copy_api_key),
        ]

        for step_name, step_func in steps:
            self.logger.info(f"▶️  Выполняется: {step_name}")
            if not step_func():
                self.logger.error(f"❌ Шаг '{step_name}' завершился с ошибкой")
                return False
        return True

    def cleanup(self):
        if self.driver:
            self.driver.quit()
            self.logger.info("Браузер закрыт")


def perform_pyannote_regeneration() -> Optional[str]:
    """
    Выполняет полную регистрацию на pyannote.ai и возвращает новый API-ключ.
    При успехе обновляет config_pyannote.py и возвращает ключ, иначе None.
    """
    if not SELENIUM_AVAILABLE:
        logger.error("Selenium не установлен, невозможно перерегистрировать API-ключ")
        return None

    logger.info("="*60)
    logger.info("🔄 НАЧАЛО АВТОМАТИЧЕСКОЙ ПЕРЕРЕГИСТРАЦИИ API-КЛЮЧА")
    logger.info("="*60)

    mail_client = TempMailClient()
    email = mail_client.create_account()
    if not email:
        logger.error("Не удалось создать временную почту")
        return None

    logger.info(f"✅ Создана временная почта: {email}")

    registrar = PyannoteAutoRegister(email, logger)
    try:
        registrar._init_driver()

        # Шаг 1: ввод email
        if not registrar.step_1_enter_email():
            logger.error("Ошибка на шаге ввода email")
            return None

        # Ожидание кода
        logger.info("⏳ Ожидание кода подтверждения (до 5 минут)...")
        code = mail_client.wait_for_pyannote_code(timeout=300)
        if not code:
            logger.error("Не удалось получить код подтверждения")
            return None

        logger.info(f"✅ Получен код: {code}")

        # Продолжение регистрации
        if not registrar.run_registration(code):
            logger.error("Регистрация не удалась")
            return None

        if not registrar.api_key:
            logger.error("API-ключ не получен")
            return None

        logger.info(f"✅ Получен новый API-ключ: {registrar.api_key[:30]}...")

        # Обновление конфигурационного файла
        if update_api_key_in_config(registrar.api_key):
            logger.info("✅ API-ключ успешно обновлён в config_pyannote.py")
            return registrar.api_key
        else:
            logger.error("Не удалось обновить конфигурационный файл")
            return None

    except Exception as e:
        logger.error(f"❌ Критическая ошибка при перерегистрации: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None
    finally:
        registrar.cleanup()


def update_api_key_in_config(new_key: str) -> bool:
    """
    Обновляет API-ключ в файле config_pyannote.py.
    Возвращает True при успехе.
    """
    config_file = Path(__file__).parent / "config_pyannote.py"
    if not config_file.exists():
        logger.error(f"Конфигурационный файл не найден: {config_file}")
        return False

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        api_key_line_index = -1
        in_pyannote_config = False
        for i, line in enumerate(lines):
            if "PYANNOTE_CONFIG" in line and "=" in line:
                in_pyannote_config = True
            elif in_pyannote_config and "}" in line:
                in_pyannote_config = False
            elif in_pyannote_config and '"api_key":' in line:
                api_key_line_index = i
                break

        if api_key_line_index >= 0:
            old_line = lines[api_key_line_index]
            indent_match = re.match(r'(\s*)"api_key":\s*"[^"]*",?', old_line)
            if indent_match:
                indent = indent_match.group(1)
                new_line = f'{indent}"api_key": "{new_key}",\n'
                lines[api_key_line_index] = new_line

                with open(config_file, "w", encoding="utf-8") as f:
                    f.writelines(lines)

                logger.info(f"✅ API-ключ обновлён в {config_file}")
                return True
            else:
                logger.error("Не удалось разобрать формат строки с API ключом")
                return False
        else:
            logger.error("Не найдена строка с API ключом в конфигурационном файле")
            return False

    except Exception as e:
        logger.error(f"Ошибка при обновлении конфигурационного файла: {e}")
        return False


# ============================================================================
# ОСНОВНОЙ КОД ПРОЦЕССОРА
# ============================================================================

class PyannoteClient:
    """Клиент для работы с API pyannote.ai"""

    def __init__(self, api_key: str, api_url: str = "https://api.pyannote.ai/v1"):
        self.api_key = api_key
        self.api_url = api_url.rstrip('/')
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        self.timeout = 60

        logger.info("✅ Pyannote клиент инициализирован")

    def diarize_from_url(self, audio_url: str, num_speakers: Optional[int] = None) -> Optional[List[Dict]]:
        """Диаризация по публичному URL"""
        try:
            logger.info(f"🔗 Запуск диаризации по URL")
            logger.info(f"📁 URL файла: {audio_url[:100]}...")

            data = {
                "url": audio_url,
                "exclusive": True,
                "confidence": True,
                "turnLevelConfidence": True
            }

            if num_speakers is not None:
                data["numSpeakers"] = num_speakers
                logger.info(f"📋 Параметры диаризации: numSpeakers={num_speakers}")
            else:
                logger.info(f"📋 Параметры диаризации: numSpeakers=авто")

            response = requests.post(
                f"{self.api_url}/diarize",
                headers=self.headers,
                json=data,
                timeout=self.timeout
            )

            if response.status_code != 200:
                logger.error(f"❌ Ошибка создания задачи: {response.status_code}")
                logger.error(f"📄 Ответ: {response.text}")
                return None

            result = response.json()
            job_id = result.get("jobId")

            if not job_id:
                logger.error("❌ Не получен ID задачи")
                return None

            logger.info(f"✅ Задача создана: {job_id}")

            job_result = self.wait_for_job_completion(job_id)
            if not job_result:
                logger.error("❌ Не удалось получить результат")
                return None

            segments = self.extract_diarization_segments(job_result)

            if segments:
                logger.info(f"📊 Получено {len(segments)} сегментов")
                self._log_diarization_stats(segments)

            return segments

        except Exception as e:
            logger.error(f"❌ Ошибка диаризации: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    def wait_for_job_completion(self, job_id: str, timeout: int = 1800, interval: int = 10) -> Optional[Dict]:
        """Ожидает завершения задачи"""
        start_time = time.time()

        logger.info(f"⏳ Ожидание завершения (ID: {job_id})...")

        while time.time() - start_time < timeout:
            try:
                response = requests.get(
                    f"{self.api_url}/jobs/{job_id}",
                    headers=self.headers,
                    timeout=self.timeout
                )

                if response.status_code != 200:
                    time.sleep(interval)
                    continue

                job_data = response.json()
                status = job_data.get("status")

                if status == "succeeded":
                    elapsed = time.time() - start_time
                    logger.info(f"✅ Диаризация успешно завершена за {elapsed:.1f} секунд")
                    return job_data
                elif status in ["failed", "canceled"]:
                    logger.error(f"❌ Диаризация завершилась со статусом: {status}")
                    return None
                else:
                    time.sleep(interval)

            except Exception as e:
                logger.warning(f"⚠️  Ошибка при ожидании: {e}")
                time.sleep(interval)

        logger.error(f"❌ Таймаут ожидания ({timeout} сек)")
        return None

    def extract_diarization_segments(self, job_result: Dict) -> List[Dict]:
        """Извлекает сегменты диаризации"""
        try:
            output = job_result.get("output", {})
            segments = output.get("exclusiveDiarization", []) or output.get("diarization", [])

            formatted_segments = []

            for segment in segments:
                formatted_segments.append({
                    "speaker": segment.get("speaker", "UNKNOWN"),
                    "start": float(segment.get("start", 0)),
                    "end": float(segment.get("end", 0)),
                    "duration": float(segment.get("end", 0)) - float(segment.get("start", 0)),
                    "confidence": segment.get("confidence", {})
                })

            formatted_segments.sort(key=lambda x: x["start"])
            return formatted_segments

        except Exception as e:
            logger.error(f"❌ Ошибка извлечения сегментов: {e}")
            return []

    def _log_diarization_stats(self, segments: List[Dict]):
        """Логирует статистику диаризации"""
        if not segments:
            return

        total_duration = sum(seg['duration'] for seg in segments)

        speaker_stats = {}
        for seg in segments:
            speaker = seg['speaker']
            if speaker not in speaker_stats:
                speaker_stats[speaker] = {'count': 0, 'duration': 0.0}
            speaker_stats[speaker]['count'] += 1
            speaker_stats[speaker]['duration'] += seg['duration']

        logger.info(f"📈 СТАТИСТИКА ДИАРИЗАЦИИ:")
        logger.info(f"  • Всего сегментов: {len(segments)}")
        logger.info(f"  • Общая длительность речи: {total_duration:.1f} сек")
        logger.info(f"  • Уникальных спикеров: {len(speaker_stats)}")

        for speaker, stats in sorted(speaker_stats.items()):
            logger.info(f"    👤 {speaker}: {stats['count']} сегментов, {stats['duration']:.1f} сек")


class AudioProcessorUnified:
    """Универсальный процессор аудио с поддержкой больших файлов"""

    def __init__(self):
        self.base_dir = Path(__file__).parent
        self.input_dir = self.base_dir / "input_files"
        self.chunk_files_dir = self.base_dir / "chunk_files"
        self.context_files_dir = Path("D:/VOQO/workers/context_files")   # ← изменено: новая папка для контекстных чанков
        self.temp_dir = None

        # Настройка параметров обработки
        self.setup_configuration()

        # Параметры для разбивки больших файлов
        self.telegram_file_size_limit_mb = 20  # <--- ИЗМЕНЕНО: 45 -> 20 МБ (лимит для голосовых сообщений)
        self.chunk_duration_sec = 600          # Длина чанка 10 минут (WAV ~18.75 МБ)
        self.chunk_overlap_sec = 60            # Перекрытие между чанками 60 секунд
        self.speaker_matching_threshold = 2.0  # Порог пересечения для сопоставления спикеров (сек)

        # Поддерживаемые форматы
        self.supported_formats = {
            '.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.mp4', '.avi',
            '.mov', '.mkv', '.mpeg', '.mpg', '.m4v', '.3gp', '.webm'
        }

        # Создаем выходные директории
        self.chunk_files_dir.mkdir(exist_ok=True)
        self.context_files_dir.mkdir(parents=True, exist_ok=True)   # ← изменено

        # Инициализация клиентов
        self.pyannote_client = None
        self.upload_manager = None
        self.pyannote_available = False

        if PYANNOTE_AVAILABLE:
            self.init_pyannote_client()
            self.init_telegram_uploader()

        logger.info("Инициализация AudioProcessorUnified завершена")
        logger.info(f"Входная папка: {self.input_dir}")
        logger.info(f"Выходная папка для чанков: {self.chunk_files_dir}")
        logger.info(f"Выходная папка для контекстных чанков: {self.context_files_dir}")
        logger.info(f"Pyannote диаризация: {'✅ ДА' if self.pyannote_available else '❌ НЕТ'}")
        logger.info(f"Telegram загрузчик: {'✅ ДА' if self.upload_manager else '❌ НЕТ'}")
        logger.info(f"Лимит Telegram: {self.telegram_file_size_limit_mb} МБ, чанки по {self.chunk_duration_sec} сек с перекрытием {self.chunk_overlap_sec} сек")

    def setup_configuration(self):
        """Настройка параметров обработки"""
        # Основные параметры
        self.target_sample_rate = 16000
        self.target_bit_depth = 16

        # Параметры для сохранения ВСЕХ сегментов
        self.min_segment_duration = 0.1  # Сохраняем всё от 0.1 секунды
        self.short_segment_threshold = 0.3  # Помечаем как SHORT

        # Параметры объединения сегментов
        self.merge_gap_same_speaker = 0.5  # Объединять сегменты одного спикера с паузой менее 0.5 сек
        self.merge_gap_channels = 0.3  # Объединять сегменты в каналах

        # Параметры детектирования речи
        self.silence_threshold = -35
        self.silence_duration = 0.3
        self.long_silence_threshold = 0.8  # Для разделения длинных пауз
        self.silence_padding = 0.1

        # Параметры анализа каналов
        self.rms_threshold = 0.005  # Более чувствительный порог
        self.correlation_threshold = 0.85  # Более низкий порог для разных каналов
        self.min_audio_length = 0.5

        # Параметры для плохих записей
        self.adaptive_processing = True
        self.min_snr_db = 6  # Минимальное отношение сигнал/шум

        # Параметры диаризации
        self.diarization_num_speakers = None  # Автоматическое определение

        # Параметры повторных попыток
        self.max_diarization_retries = 3
        self.retry_delay_seconds = 10

        # Параметры для контекстных чанков
        self.context_chunk_length = 25   # Длина чанка для транскрибации
        self.context_chunk_overlap = 5   # Перекрытие между чанками

    def init_pyannote_client(self):
        """Инициализация клиента pyannote"""
        try:
            self.pyannote_client = PyannoteClient(
                api_key=PYANNOTE_CONFIG["api_key"],
                api_url=PYANNOTE_CONFIG["api_url"]
            )
            self.pyannote_available = True
            logger.info("✅ Pyannote клиент инициализирован")
        except Exception as e:
            logger.error(f"Ошибка инициализации Pyannote: {e}")
            self.pyannote_client = None
            self.pyannote_available = False

    def init_telegram_uploader(self):
        """Инициализация Telegram загрузчика"""
        try:
            from upload_service import UploadManager
            self.upload_manager = UploadManager(
                telegram_token=TELEGRAM_CONFIG["bot_token"],
                telegram_chat_id=TELEGRAM_CONFIG["chat_id"]
            )
            logger.info("✅ Telegram UploadManager инициализирован")
        except ImportError as e:
            logger.error(f"❌ Не удалось импортировать upload_service: {e}")
            self.upload_manager = None
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации Telegram: {e}")
            self.upload_manager = None

    def create_temp_directory(self) -> Path:
        """Создание временной директории"""
        try:
            self.temp_dir = Path(tempfile.mkdtemp(prefix="audio_processor_"))
            logger.info(f"Создана временная директория: {self.temp_dir}")
            return self.temp_dir
        except Exception as e:
            self.temp_dir = self.base_dir / "temp_processing"
            self.temp_dir.mkdir(exist_ok=True)
            logger.info(f"Создана временная директория (fallback): {self.temp_dir}")
            return self.temp_dir

    def cleanup_temp_directory(self):
        """Очистка временной директории"""
        if self.temp_dir and self.temp_dir.exists():
            try:
                shutil.rmtree(self.temp_dir)
                logger.info(f"Очищена временная директория: {self.temp_dir}")
            except Exception as e:
                logger.warning(f"Не удалось очистить временную директорию: {e}")

    def check_dependencies(self) -> bool:
        """Проверка наличия необходимых зависимостей"""
        try:
            subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True, timeout=5)
            logger.info("✓ FFmpeg доступен")

            import numpy as np
            import librosa
            logger.info("✓ numpy и librosa доступны")

            return True
        except Exception as e:
            logger.error(f"✗ Ошибка проверки зависимостей: {e}")
            return False

    def has_audio_stream(self, file_path: Path) -> bool:
        """Проверяет, содержит ли файл аудиодорожки"""
        try:
            cmd = [
                'ffprobe', '-v', 'error',
                '-select_streams', 'a',
                '-show_entries', 'stream=codec_type',
                '-of', 'json',
                str(file_path)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            probe_data = json.loads(result.stdout)
            return 'streams' in probe_data and len(probe_data['streams']) > 0
        except Exception as e:
            logger.warning(f"Ошибка проверки аудио: {e}")
            return False

    def convert_to_wav(self, input_file: Path, output_file: Path) -> bool:
        """Конвертирует файл в WAV с сохранением каналов"""
        try:
            logger.info(f"Конвертация: {input_file.name} -> {output_file.name}")

            (
                ffmpeg
                .input(str(input_file))
                .output(
                    str(output_file),
                    acodec='pcm_s16le',
                    ar=self.target_sample_rate,
                    **{'y': None}
                )
                .overwrite_output()
                .run(quiet=True, capture_stderr=True)
            )

            if output_file.exists():
                file_size_mb = output_file.stat().st_size / (1024 * 1024)
                logger.info(f"✓ Конвертация успешна: {file_size_mb:.2f} МБ")
                return True
            else:
                logger.error(f"✗ Не удалось создать файл")
                return False
        except ffmpeg.Error as e:
            logger.error(f"Ошибка конвертации: {e.stderr.decode() if e.stderr else str(e)}")
            return False
        except Exception as e:
            logger.error(f"Неожиданная ошибка: {e}")
            return False

    def get_audio_info(self, file_path: Path) -> Dict:
        """Получает информацию об аудиофайле"""
        try:
            probe = ffmpeg.probe(str(file_path))
            audio_stream = next(
                (stream for stream in probe['streams'] if stream['codec_type'] == 'audio'),
                None
            )

            if not audio_stream:
                return None

            return {
                'channels': int(audio_stream.get('channels', 1)),
                'sample_rate': int(audio_stream.get('sample_rate', 0)),
                'duration': float(audio_stream.get('duration', 0)),
                'codec': audio_stream.get('codec_name', 'unknown')
            }
        except Exception as e:
            logger.error(f"Ошибка получения информации: {e}")
            return None

    def analyze_recording_quality(self, file_path: Path) -> Dict:
        """Анализирует качество записи"""
        try:
            import numpy as np
            import librosa

            quality_info = {
                'quality': 'unknown',
                'snr_db': 0,
                'is_noisy': False,
                'recommended_strategy': 'standard'
            }

            # Загружаем аудио
            y, sr = librosa.load(str(file_path), sr=8000, mono=True)

            if len(y) < 100:
                return quality_info

            # Рассчитываем отношение сигнал/шум
            signal_power = np.mean(y**2)
            noise_estimate = np.percentile(np.abs(y), 25)
            snr_db = 10 * np.log10(signal_power / (noise_estimate**2 + 1e-10))

            quality_info['snr_db'] = snr_db

            # Определяем качество
            if snr_db > 20:
                quality_info['quality'] = 'good'
                quality_info['recommended_strategy'] = 'standard'
            elif snr_db > 10:
                quality_info['quality'] = 'medium'
                quality_info['recommended_strategy'] = 'sensitive'
                quality_info['is_noisy'] = True
            else:
                quality_info['quality'] = 'poor'
                quality_info['recommended_strategy'] = 'aggressive'
                quality_info['is_noisy'] = True

            logger.info(f"📊 Качество записи: {quality_info['quality']} (SNR: {snr_db:.1f} dB)")
            return quality_info

        except Exception as e:
            logger.warning(f"Ошибка анализа качества: {e}")
            return {'quality': 'unknown', 'recommended_strategy': 'standard'}

    def split_audio_channels(self, input_file: Path) -> List[Path]:
        """Разделяет аудиофайл на отдельные каналы"""
        try:
            audio_info = self.get_audio_info(input_file)
            if not audio_info:
                return []

            num_channels = audio_info['channels']
            base_name = input_file.stem

            logger.info(f"Разделение на {num_channels} канал(ов)")

            channel_files = []

            if num_channels == 1:
                channel_file = self.temp_dir / f"{base_name}_channel1.wav"
                if input_file != channel_file:
                    shutil.copy2(input_file, channel_file)
                channel_files.append(channel_file)
            else:
                for channel in range(num_channels):
                    channel_file = self.temp_dir / f"{base_name}_channel{channel + 1}.wav"

                    (
                        ffmpeg
                        .input(str(input_file))
                        .output(
                            str(channel_file),
                            af=f'pan=mono|c0=c{channel}',
                            acodec='pcm_s16le',
                            ac=1,
                            ar=self.target_sample_rate
                        )
                        .overwrite_output()
                        .run(quiet=True, capture_stderr=True)
                    )

                    if channel_file.exists():
                        channel_files.append(channel_file)
                        logger.info(f"Создан канал {channel + 1}")

            return channel_files

        except Exception as e:
            logger.error(f"Ошибка разделения каналов: {e}")
            return []

    def analyze_channels(self, channel_files: List[Path]) -> Dict:
        """Анализирует каналы аудиофайла"""
        analysis_result = {
            'total_channels': len(channel_files),
            'active_channels': 0,
            'unique_channels': 0,
            'channel_names': [],
            'recommendation': 'unknown',
            'reason': '',
            'is_stereo_different': False
        }

        try:
            import numpy as np
            import librosa

            logger.info(f"🔍 Анализ {len(channel_files)} канала(ов)...")

            channel_data = []
            for i, channel_file in enumerate(channel_files):
                try:
                    y, sr = librosa.load(str(channel_file), sr=8000, mono=True)

                    duration = len(y) / sr
                    if duration < self.min_audio_length:
                        continue

                    rms = np.sqrt(np.mean(y**2))
                    is_active = rms > self.rms_threshold

                    channel_info = {
                        'index': i,
                        'file': channel_file,
                        'rms': rms,
                        'is_active': is_active,
                        'channel_name': f'channel{i+1}'
                    }

                    channel_data.append(channel_info)

                    if is_active:
                        analysis_result['active_channels'] += 1
                        logger.info(f"  Канал {i+1}: АКТИВЕН (RMS={rms:.4f})")
                    else:
                        logger.info(f"  Канал {i+1}: НЕАКТИВЕН (RMS={rms:.4f})")

                except Exception as e:
                    logger.error(f"  Ошибка анализа канала {i+1}: {e}")

            # Определяем имена каналов
            if len(channel_data) == 1:
                analysis_result['channel_names'] = ['mono']
            elif len(channel_data) == 2:
                analysis_result['channel_names'] = ['left', 'right']
            else:
                analysis_result['channel_names'] = [f'channel{i+1}' for i in range(len(channel_data))]

            # Анализируем корреляцию для стерео
            if analysis_result['active_channels'] >= 2:
                active_channels = [c for c in channel_data if c['is_active']]

                if len(active_channels) >= 2:
                    correlations = []
                    for i in range(len(active_channels)):
                        for j in range(i + 1, len(active_channels)):
                            try:
                                y1, sr1 = librosa.load(str(active_channels[i]['file']), sr=8000, mono=True)
                                y2, sr2 = librosa.load(str(active_channels[j]['file']), sr=8000, mono=True)

                                min_len = min(len(y1), len(y2))
                                if min_len > 10:
                                    y1 = y1[:min_len]
                                    y2 = y2[:min_len]
                                    correlation = np.corrcoef(y1, y2)[0, 1]
                                    correlations.append(correlation)

                                    logger.info(f"  📊 Корреляция каналов {i+1}-{j+1}: {correlation:.3f}")
                            except Exception as e:
                                continue

                    if correlations:
                        avg_correlation = np.mean(correlations)
                        if avg_correlation < self.correlation_threshold:
                            analysis_result['recommendation'] = 'channel_based'
                            analysis_result['reason'] = f'Каналы разные (корреляция={avg_correlation:.3f})'
                            analysis_result['is_stereo_different'] = True
                            analysis_result['unique_channels'] = len(active_channels)
                            logger.info(f"  ✅ Каналы РАЗНЫЕ -> обработка по каналам")
                        else:
                            analysis_result['recommendation'] = 'diarization'
                            analysis_result['reason'] = f'Каналы одинаковые (корреляция={avg_correlation:.3f})'
                            logger.info(f"  🔄 Каналы ОДИНАКОВЫЕ -> диаризация")
                    else:
                        analysis_result['recommendation'] = 'diarization'
                        analysis_result['reason'] = 'Не удалось сравнить каналы'
                else:
                    analysis_result['recommendation'] = 'diarization'
                    analysis_result['reason'] = 'Меньше 2 активных каналов'
            else:
                analysis_result['recommendation'] = 'diarization'
                analysis_result['reason'] = f'Активных каналов: {analysis_result["active_channels"]}'

            return analysis_result

        except ImportError as e:
            logger.error(f"Ошибка импорта для анализа: {e}")
            analysis_result['recommendation'] = 'diarization'
            analysis_result['reason'] = 'Ошибка анализа'
            return analysis_result
        except Exception as e:
            logger.error(f"Ошибка анализа каналов: {e}")
            analysis_result['recommendation'] = 'diarization'
            analysis_result['reason'] = f'Исключение: {str(e)}'
            return analysis_result

    def _detect_speech_segments_ffmpeg(self, file_path: Path, quality: str = 'standard') -> List[Dict]:
        """Детектирует сегменты речи в аудиофайле"""
        try:
            logger.info(f"Поиск речевых сегментов в: {file_path.name}")

            # Адаптивные параметры в зависимости от качества
            if quality == 'sensitive':
                threshold = -38
                duration = 0.4
            elif quality == 'aggressive':
                threshold = -42
                duration = 0.5
            else:  # standard
                threshold = -35
                duration = 0.3

            cmd = [
                'ffmpeg',
                '-i', str(file_path),
                '-af', f'silencedetect=noise={threshold}dB:d={duration}',
                '-f', 'null',
                '-'
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            output = result.stderr

            silence_periods = []
            lines = output.split('\n')
            i = 0

            while i < len(lines):
                if 'silence_start' in lines[i]:
                    start_match = re.search(r'silence_start:\s*([0-9.]+)', lines[i])
                    if start_match:
                        start = float(start_match.group(1))

                        for j in range(i, len(lines)):
                            if 'silence_end' in lines[j]:
                                end_match = re.search(r'silence_end:\s*([0-9.]+)', lines[j])
                                dur_match = re.search(r'silence_duration:\s*([0-9.]+)', lines[j])
                                if end_match and dur_match:
                                    end = float(end_match.group(1))
                                    duration_silence = float(dur_match.group(1))
                                    silence_periods.append({
                                        'start': start,
                                        'end': end,
                                        'duration': duration_silence
                                    })
                                    i = j
                                    break
                i += 1

            logger.info(f"Найдено {len(silence_periods)} периодов тишины")

            # Преобразуем периоды тишины в периоды речи
            audio_info = self.get_audio_info(file_path)
            if not audio_info:
                return []

            total_duration = audio_info['duration']
            speech_segments = self._silence_to_speech_segments(silence_periods, total_duration)

            logger.info(f"Найдено {len(speech_segments)} речевых сегментов")
            return speech_segments

        except Exception as e:
            logger.error(f"Ошибка при детектировании речи: {e}")
            return []

    def detect_speech_segments(self, file_path: Path, quality: str = 'standard') -> List[Dict]:
        """Детектирует сегменты речи с помощью Silero VAD.
        quality не используется (оставлен для совместимости)."""
        try:
            import soundfile as sf
            audio, sr = sf.read(str(file_path))
            # Приводим к float32 и моно (если стерео)
            if audio.ndim == 1:
                audio = audio.astype('float32')
            else:
                audio = audio.mean(axis=1).astype('float32')
            
            vad = SileroVAD(sample_rate=sr)
            segments = vad.get_speech_segments(audio, pad_ms=100)
            
            result = []
            for seg in segments:
                result.append({
                    'start': seg['start'],
                    'end': seg['end'],
                    'duration': seg['end'] - seg['start']
                })
            logger.info(f"Silero VAD: найдено {len(result)} речевых сегментов")
            return result
        except Exception as e:
            logger.error(f"Ошибка Silero VAD: {e}, пробую fallback ffmpeg")
            return self._detect_speech_segments_ffmpeg(file_path, quality)

    def _silence_to_speech_segments(self, silence_periods: List[Dict], total_duration: float) -> List[Dict]:
        """Преобразует периоды тишины в периоды речи"""
        silence_periods.sort(key=lambda x: x['start'])

        # Фильтруем только длинные паузы
        long_silences = [s for s in silence_periods if s['duration'] >= self.long_silence_threshold]

        if not long_silences:
            # Нет длинных пауз - весь файл как один сегмент
            return [{
                'start': 0,
                'end': total_duration,
                'duration': total_duration
            }]

        speech_segments = []

        # От начала до первой длинной паузы
        if long_silences[0]['start'] > 0:
            speech_segments.append({
                'start': max(0, 0 - self.silence_padding),
                'end': min(total_duration, long_silences[0]['start'] + self.silence_padding),
                'duration': long_silences[0]['start'] + 2 * self.silence_padding
            })

        # Между длинными паузами
        for i in range(len(long_silences) - 1):
            start = long_silences[i]['end']
            end = long_silences[i + 1]['start']

            if end - start > 0.01:  # Есть хоть какая-то речь
                speech_segments.append({
                    'start': max(0, start - self.silence_padding),
                    'end': min(total_duration, end + self.silence_padding),
                    'duration': (end - start) + 2 * self.silence_padding
                })

        # От последней длинной паузы до конца
        if long_silences[-1]['end'] < total_duration:
            speech_segments.append({
                'start': max(0, long_silences[-1]['end'] - self.silence_padding),
                'end': min(total_duration, total_duration + self.silence_padding),
                'duration': (total_duration - long_silences[-1]['end']) + 2 * self.silence_padding
            })

        # Объединяем перекрывающиеся сегменты
        if speech_segments:
            speech_segments.sort(key=lambda x: x['start'])
            merged = []
            current = speech_segments[0].copy()

            for segment in speech_segments[1:]:
                if segment['start'] <= current['end'] + 0.1:
                    current['end'] = max(current['end'], segment['end'])
                    current['duration'] = current['end'] - current['start']
                else:
                    merged.append(current)
                    current = segment.copy()

            merged.append(current)
            speech_segments = merged

        return speech_segments

    def _merge_adjacent_segments(self, segments: List[Dict], max_gap: float) -> List[Dict]:
        """Объединяет близкие сегменты"""
        if not segments:
            return []

        segments.sort(key=lambda x: x['start'])
        merged = []
        current = segments[0].copy()

        for seg in segments[1:]:
            # Если промежуток меньше max_gap, объединяем
            if seg['start'] - current['end'] <= max_gap:
                current['end'] = seg['end']
                current['duration'] = current['end'] - current['start']
            else:
                merged.append(current)
                current = seg.copy()

        merged.append(current)
        return merged

    def _get_speaker_number(self, source: str, method: str) -> int:
        """
        Получает номер спикера из source и method.

        Args:
            source: источник ('left', 'right', 'SPEAKER_00', 'mono', etc.)
            method: метод обработки ('channel', 'diarized', 'mono')

        Returns:
            Номер спикера (1, 2, 3...)
        """
        try:
            if method == 'channel':
                # Для каналов: left -> спикер1, right -> спикер2, channelX -> спикерX
                if source == 'left':
                    return 1
                elif source == 'right':
                    return 2
                elif source.startswith('channel'):
                    return int(source.replace('channel', ''))
                else:
                    return 1
            elif method == 'diarized':
                # Для диаризации: SPEAKER_00 -> спикер1, SPEAKER_01 -> спикер2 и т.д.
                if source.startswith('SPEAKER_'):
                    speaker_num = int(source.split('_')[1])
                    return speaker_num + 1
                else:
                    return 1
            else:  # 'mono' и другие методы
                return 1
        except Exception:
            return 1

    def _format_time_for_filename(self, seconds: float) -> str:
        """
        Форматирует время в строку для имени файла.
        Формат: HHMMSSmmm (часы-минуты-секунды-миллисекунды)
        Пример: 3723.456 -> 010323456 (1 час 2 минуты 3 секунды 456 мс)
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)

        # Форматируем с ведущими нулями
        return f"{hours:02d}{minutes:02d}{secs:02d}{millis:03d}"

    def create_unified_chunks(self, audio_file: Path, segments: List[Dict],
                             base_name: str, method: str, source: str) -> List[Path]:
        """Создает чанки с единым стандартом именования"""
        chunks = []

        # Очищаем базовое имя от специальных символов и ограничиваем длину
        base_name_clean = re.sub(r'[<>:"/\\|?*]', '_', base_name)

        # Ограничиваем длину имени файла для совместимости с файловыми системами
        max_base_name_length = 50
        if len(base_name_clean) > max_base_name_length:
            # Сохраняем начало и конец имени файла
            half_len = max_base_name_length // 2
            base_name_clean = base_name_clean[:half_len] + "..." + base_name_clean[-half_len:]

        # Сортируем сегменты по времени
        segments.sort(key=lambda x: x['start'])

        for idx, segment in enumerate(segments, 1):
            start_time = segment.get('start', 0)
            end_time = segment.get('end', 0)
            duration = end_time - start_time

            # Проверяем минимальную длительность
            if duration < self.min_segment_duration:
                logger.debug(f"Пропускаем очень короткий сегмент: {duration:.2f} сек")
                continue

            # Определяем, короткий ли сегмент
            is_short = duration < self.short_segment_threshold

            # Получаем номер спикера
            speaker_num = self._get_speaker_number(source, method)

            # Форматируем время для имени файла
            start_str = self._format_time_for_filename(start_time)
            end_str = self._format_time_for_filename(end_time)

            # Создаем имя файла с единым форматом: имя_спикерX_начало-конец.wav
            output_filename = f"{base_name_clean}_спикер{speaker_num}_{start_str}-{end_str}.wav"
            output_path = self.chunk_files_dir / output_filename

            # Проверяем, существует ли уже файл
            if output_path.exists():
                # Добавляем суффикс для уникальности
                suffix = 1
                while output_path.exists():
                    output_filename = f"{base_name_clean}_спикер{speaker_num}_{start_str}-{end_str}_{suffix}.wav"
                    output_path = self.chunk_files_dir / output_filename
                    suffix += 1

            # Создаем чанк
            try:
                (
                    ffmpeg
                    .input(str(audio_file), ss=start_time, t=duration)
                    .output(
                        str(output_path),
                        acodec='pcm_s16le',
                        ac=1,  # Всегда моно для чанков
                        ar=self.target_sample_rate
                    )
                    .overwrite_output()
                    .run(quiet=True, capture_stderr=True)
                )

                if output_path.exists():
                    chunk_size_kb = output_path.stat().st_size / 1024

                    # Показываем реальное время в логах для удобства
                    hours_start = start_time // 3600
                    minutes_start = (start_time % 3600) // 60
                    seconds_start = start_time % 60

                    hours_end = end_time // 3600
                    minutes_end = (end_time % 3600) // 60
                    seconds_end = end_time % 60

                    time_format_start = f"{int(hours_start):02d}:{int(minutes_start):02d}:{seconds_start:05.2f}"
                    time_format_end = f"{int(hours_end):02d}:{int(minutes_end):02d}:{seconds_end:05.2f}"

                    log_msg = f"{'🟡' if is_short else '✅'} Чанк {idx}: {output_filename} ({duration:.2f} сек, {chunk_size_kb:.1f} КБ)"
                    log_msg += f" [время: {time_format_start}-{time_format_end}]"

                    if is_short:
                        logger.debug(log_msg)
                    else:
                        logger.info(log_msg)

                    chunks.append(output_path)
                else:
                    logger.error(f"❌ Не удалось создать файл: {output_filename}")

            except Exception as e:
                logger.error(f"❌ Ошибка создания чанка {idx}: {e}")
                continue

        return chunks

    def create_context_chunks(self, audio_file: Path, base_name: str) -> Dict:
        """Создаёт контекстные чанки (фиксированная длина, перекрытие) в папке context_files.
        Имя файла: {base_name}_context_{start}-{end}.wav
        """
        result = {
            'chunks_created': 0,
            'errors': [],
            'warnings': []
        }

        try:
            logger.info(f"🎵 Создание контекстных чанков: {audio_file.name}")

            if not audio_file.exists():
                error_msg = f"Файл не найден: {audio_file}"
                result['errors'].append(error_msg)
                return result

            audio_info = self.get_audio_info(audio_file)
            if not audio_info:
                error_msg = f"Не удалось получить информацию об аудиофайле: {audio_file}"
                result['errors'].append(error_msg)
                return result

            total_duration = audio_info['duration']
            logger.info(f"📊 Длительность файла для контекста: {total_duration:.2f} сек")

            # Если файл слишком короткий, создаем один чанк
            if total_duration <= self.context_chunk_length:
                segments = [{
                    'start': 0,
                    'end': total_duration,
                    'duration': total_duration
                }]
                logger.info(f"📊 Файл короткий, создаем 1 чанк")
            else:
                # Создаем сегменты по self.context_chunk_length секунд с перекрытием
                segments = []
                start = 0
                chunk_num = 1

                while start < total_duration:
                    end = min(start + self.context_chunk_length, total_duration)
                    segments.append({
                        'start': start,
                        'end': end,
                        'duration': end - start
                    })

                    logger.debug(f"  Чанк {chunk_num}: {start:.1f} - {end:.1f} сек ({end-start:.1f} сек)")
                    start += (self.context_chunk_length - self.context_chunk_overlap)
                    chunk_num += 1

            logger.info(f"📊 Нарезано {len(segments)} сегментов для контекста")

            # Очищаем базовое имя
            base_name_clean = re.sub(r'[<>:"/\\|?*]', '_', base_name)
            if len(base_name_clean) > 50:
                half_len = 50 // 2
                base_name_clean = base_name_clean[:half_len] + "..." + base_name_clean[-half_len:]

            # Создаем чанки в папке context_files
            for idx, segment in enumerate(segments, 1):
                start_time = segment['start']
                end_time = segment['end']
                duration = segment['duration']

                start_str = self._format_time_for_filename(start_time)
                end_str = self._format_time_for_filename(end_time)

                # Имя файла: имя_контекст_начало-конец.wav
                output_filename = f"{base_name_clean}_context_{start_str}-{end_str}.wav"
                output_path = self.context_files_dir / output_filename

                # Уникальность
                if output_path.exists():
                    suffix = 1
                    while output_path.exists():
                        output_filename = f"{base_name_clean}_context_{start_str}-{end_str}_{suffix}.wav"
                        output_path = self.context_files_dir / output_filename
                        suffix += 1

                try:
                    (
                        ffmpeg
                        .input(str(audio_file), ss=start_time, t=duration)
                        .output(
                            str(output_path),
                            acodec='pcm_s16le',
                            ac=1,  # Моно
                            ar=self.target_sample_rate
                        )
                        .overwrite_output()
                        .run(quiet=True, capture_stderr=True)
                    )

                    if output_path.exists():
                        chunk_size_kb = output_path.stat().st_size / 1024

                        hours_start = start_time // 3600
                        minutes_start = (start_time % 3600) // 60
                        seconds_start = start_time % 60

                        hours_end = end_time // 3600
                        minutes_end = (end_time % 3600) // 60
                        seconds_end = end_time % 60

                        time_format_start = f"{int(hours_start):02d}:{int(minutes_start):02d}:{seconds_start:05.2f}"
                        time_format_end = f"{int(hours_end):02d}:{int(minutes_end):02d}:{seconds_end:05.2f}"

                        logger.info(f"🎵 Контекстный чанк {idx}: {output_filename} ({duration:.2f} сек, {chunk_size_kb:.1f} КБ)")
                        logger.info(f"   [время: {time_format_start}-{time_format_end}]")
                        logger.info(f"   📁 Сохранен в: {self.context_files_dir}")

                        result['chunks_created'] += 1
                    else:
                        error_msg = f"Не удалось создать контекстный чанк: {output_filename}"
                        result['errors'].append(error_msg)
                        logger.error(f"❌ {error_msg}")

                except Exception as e:
                    error_msg = f"Ошибка создания контекстного чанка {idx}: {e}"
                    result['errors'].append(error_msg)
                    logger.error(f"❌ {error_msg}")

            if result['chunks_created'] > 0:
                logger.info(f"✅ Создано контекстных чанков: {result['chunks_created']}")
                logger.info(f"📁 Папка с контекстными чанками: {self.context_files_dir}")
            else:
                logger.warning(f"⚠️  Не создано ни одного контекстного чанка")

        except Exception as e:
            error_msg = f"Критическая ошибка в create_context_chunks: {e}"
            result['errors'].append(error_msg)
            logger.error(f"❌ {error_msg}")
            import traceback
            logger.error(traceback.format_exc())

        return result

    def create_mono_for_context(self, audio_file: Path, base_name: str) -> Optional[Path]:
        """Создаёт моно-версию файла для контекстных чанков"""
        try:
            mono_file = self.temp_dir / f"{base_name}_mono.wav"

            audio_info = self.get_audio_info(audio_file)
            if audio_info and audio_info['channels'] == 1:
                shutil.copy2(audio_file, mono_file)
                logger.info(f"✅ Файл уже моно, создана копия для контекста")
            else:
                logger.info(f"🎵 Конвертация в моно для контекстных чанков...")

                (
                    ffmpeg
                    .input(str(audio_file))
                    .output(
                        str(mono_file),
                        acodec='pcm_s16le',
                        ac=1,
                        ar=self.target_sample_rate,
                        **{'y': None}
                    )
                    .overwrite_output()
                    .run(quiet=True, capture_stderr=True)
                )

                if mono_file.exists():
                    file_size_mb = mono_file.stat().st_size / (1024 * 1024)
                    logger.info(f"✅ Моно-версия для контекста создана: {file_size_mb:.2f} МБ")
                else:
                    logger.error(f"❌ Не удалось создать моно-версию для контекста")
                    return None

            return mono_file

        except Exception as e:
            logger.error(f"❌ Ошибка создания моно-версии для контекста: {e}")
            return None

    def process_channel_based(self, audio_file: Path, channel_files: List[Path],
                             channel_names: List[str], base_name: str) -> List[Path]:
        """Обработка по каналам (стерео с разными голосами)"""
        all_chunks = []

        logger.info(f"⚡ Обработка по каналам ({len(channel_files)} каналов)")

        for i, (channel_file, channel_name) in enumerate(zip(channel_files, channel_names)):
            logger.info(f"📊 Обработка канала: {channel_name}")

            quality_info = self.analyze_recording_quality(channel_file)
            strategy = quality_info.get('recommended_strategy', 'standard')

            speech_segments = self.detect_speech_segments(channel_file, quality=strategy)

            if not speech_segments:
                logger.warning(f"⚠️  В канале {channel_name} не найдено речи")
                continue

            merged_segments = self._merge_adjacent_segments(speech_segments, self.merge_gap_channels)

            channel_chunks = self.create_unified_chunks(
                audio_file=channel_file,
                segments=merged_segments,
                base_name=base_name,
                method='channel',
                source=channel_name
            )

            logger.info(f"✅ Канал {channel_name}: создано {len(channel_chunks)} чанков")
            all_chunks.extend(channel_chunks)

        return all_chunks

    def renew_pyannote_api_key(self) -> bool:
        """Пересоздание API-ключа pyannote через встроенную регистрацию"""
        logger.warning("🔄 Попытка пересоздания API-ключа через автоматическую регистрацию...")

        if not SELENIUM_AVAILABLE:
            logger.error("Selenium не установлен, невозможно выполнить перерегистрацию")
            return False

        # Выполняем полную регистрацию и получаем новый ключ
        new_key = perform_pyannote_regeneration()
        if not new_key:
            logger.error("Не удалось получить новый API-ключ")
            return False

        # Обновляем конфигурацию (уже выполнено внутри perform_pyannote_regeneration)
        # Перезагружаем конфигурационный модуль
        try:
            if 'config_pyannote' in sys.modules:
                del sys.modules['config_pyannote']
            from config_pyannote import PYANNOTE_CONFIG

            self.pyannote_client = PyannoteClient(
                api_key=PYANNOTE_CONFIG["api_key"],
                api_url=PYANNOTE_CONFIG["api_url"]
            )
            logger.info("✅ Конфигурация перезагружена, клиент обновлён")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка перезагрузки конфигурации: {e}")
            return False

    # ================== МЕТОДЫ ДЛЯ РАБОТЫ С БОЛЬШИМИ ФАЙЛАМИ ==================

    def _split_audio_into_chunks(self, audio_file: Path) -> List[Dict]:
        """Разбивает аудиофайл на перекрывающиеся чанки для диаризации."""
        info = self.get_audio_info(audio_file)
        if not info:
            logger.error("Не удалось получить информацию об аудиофайле для разбивки")
            return []

        total_duration = info['duration']
        chunk_duration = self.chunk_duration_sec
        overlap = self.chunk_overlap_sec
        step = chunk_duration - overlap

        num_chunks = math.ceil((total_duration - overlap) / step)
        base_name = audio_file.stem
        chunks = []

        logger.info(f"Разбивка файла длительностью {total_duration:.1f} сек на {num_chunks} чанков по {chunk_duration} сек с перекрытием {overlap} сек")

        for i in range(num_chunks):
            start = i * step
            end = min(start + chunk_duration, total_duration)
            if end - start < 1.0:
                logger.debug(f"Чанк {i} слишком короткий ({end-start:.1f} сек), пропускаем")
                continue

            chunk_filename = self.temp_dir / f"{base_name}_chunk_{i:04d}.wav"
            try:
                (
                    ffmpeg
                    .input(str(audio_file), ss=start, t=end-start)
                    .output(
                        str(chunk_filename),
                        acodec='pcm_s16le',
                        ac=1,
                        ar=self.target_sample_rate
                    )
                    .overwrite_output()
                    .run(quiet=True, capture_stderr=True)
                )

                if chunk_filename.exists():
                    size_mb = chunk_filename.stat().st_size / (1024 * 1024)
                    logger.info(f"  Чанк {i:04d}: {start:.1f}-{end:.1f} сек, размер {size_mb:.1f} МБ")
                    chunks.append({
                        'path': chunk_filename,
                        'start': start,
                        'end': end
                    })
                else:
                    logger.error(f"  Не удалось создать чанк {i}")
            except Exception as e:
                logger.error(f"  Ошибка при создании чанка {i}: {e}")

        logger.info(f"Создано {len(chunks)} чанков")
        return chunks

    def _diarize_single_file(self, file_path: Path, file_start_time: float) -> Optional[List[Dict]]:
        """Отправляет один аудиофайл через Telegram в pyannote, получает сегменты."""
        if not self.upload_manager:
            logger.error("Telegram upload_manager не доступен")
            return None

        for attempt in range(1, self.max_diarization_retries + 1):
            try:
                logger.info(f"  🔄 Попытка {attempt} для чанка {file_path.name}")

                public_url = self.upload_manager.upload_audio_file(file_path)
                if not public_url:
                    logger.error(f"  ❌ Не удалось загрузить чанк {file_path.name}")
                    if attempt < self.max_diarization_retries:
                        self._handle_retry_delay(attempt)
                    continue

                segments = self.pyannote_client.diarize_from_url(
                    public_url,
                    num_speakers=self.diarization_num_speakers
                )

                if not segments:
                    logger.error(f"  ❌ Не удалось получить результат диаризации для чанка")
                    if attempt < self.max_diarization_retries:
                        self._handle_retry_delay(attempt)
                    continue

                for seg in segments:
                    seg['start'] += file_start_time
                    seg['end'] += file_start_time

                logger.info(f"  ✅ Чанк обработан, получено {len(segments)} сегментов")
                return segments

            except Exception as e:
                logger.error(f"  ❌ Ошибка при попытке {attempt}: {e}")
                if attempt == self.max_diarization_retries:
                    break
                self._handle_retry_delay(attempt)

        logger.error(f"  ❌ Все попытки для чанка {file_path.name} исчерпаны")
        return None

    def _match_speakers_across_chunks(self, chunks_data: List[Dict]) -> List[Dict]:
        """Сопоставляет спикеров между чанками на основе перекрытия."""
        if not chunks_data:
            return []

        chunks_data.sort(key=lambda x: x['start'])

        global_map = {}
        next_global = 1
        all_segments = []

        first_chunk = chunks_data[0]
        for seg in first_chunk['segments']:
            local_spk = seg['speaker']
            if local_spk not in global_map:
                global_map[local_spk] = next_global
                next_global += 1
            all_segments.append({
                'start': seg['start'],
                'end': seg['end'],
                'speaker_global': global_map[local_spk],
                'duration': seg['duration']
            })

        for i in range(1, len(chunks_data)):
            curr_chunk = chunks_data[i]
            prev_chunk = chunks_data[i-1]

            overlap_start = curr_chunk['start']
            overlap_end = prev_chunk['end']
            if overlap_end <= overlap_start:
                logger.warning(f"  Чанки {i-1} и {i} не перекрываются, сопоставление будет приблизительным")
                for seg in curr_chunk['segments']:
                    local_spk = seg['speaker']
                    if local_spk not in global_map:
                        global_map[local_spk] = next_global
                        next_global += 1
                    all_segments.append({
                        'start': seg['start'],
                        'end': seg['end'],
                        'speaker_global': global_map[local_spk],
                        'duration': seg['duration']
                    })
                continue

            prev_overlap = [s for s in all_segments if s['end'] > overlap_start and s['start'] < overlap_end]
            curr_overlap = [s for s in curr_chunk['segments'] if s['end'] > overlap_start and s['start'] < overlap_end]

            import numpy as np
            overlap_matrix = {}
            for seg_curr in curr_overlap:
                local_spk = seg_curr['speaker']
                if local_spk not in overlap_matrix:
                    overlap_matrix[local_spk] = {}

            for seg_curr in curr_overlap:
                local_spk = seg_curr['speaker']
                for seg_prev in prev_overlap:
                    global_spk = seg_prev['speaker_global']
                    overlap_dur = max(0, min(seg_curr['end'], seg_prev['end']) - max(seg_curr['start'], seg_prev['start']))
                    if overlap_dur > 0:
                        if global_spk not in overlap_matrix[local_spk]:
                            overlap_matrix[local_spk][global_spk] = 0
                        overlap_matrix[local_spk][global_spk] += overlap_dur

            threshold = self.speaker_matching_threshold
            used_globals = set()
            for local_spk, matches in overlap_matrix.items():
                if not matches:
                    continue
                best_global = max(matches.items(), key=lambda x: x[1])
                if best_global[1] >= threshold and best_global[0] not in used_globals:
                    global_map[local_spk] = best_global[0]
                    used_globals.add(best_global[0])

            for seg in curr_chunk['segments']:
                local_spk = seg['speaker']
                if local_spk not in global_map:
                    global_map[local_spk] = next_global
                    next_global += 1
                all_segments.append({
                    'start': seg['start'],
                    'end': seg['end'],
                    'speaker_global': global_map[local_spk],
                    'duration': seg['duration']
                })

        all_segments.sort(key=lambda x: x['start'])
        return all_segments

    def _obtain_diarization_segments(self, audio_file: Path) -> Optional[List[Dict]]:
        """Основной метод для получения сегментов диаризации с учётом разбивки."""
        file_size_mb = audio_file.stat().st_size / (1024 * 1024)
        logger.info(f"Размер файла для диаризации: {file_size_mb:.1f} МБ")

        if file_size_mb <= self.telegram_file_size_limit_mb:
            logger.info("Файл помещается в лимит Telegram, обрабатываем без разбивки")
            segments = self._diarize_single_file(audio_file, 0.0)
            if not segments:
                return None
            global_map = {}
            next_global = 1
            result = []
            for seg in segments:
                local_spk = seg['speaker']
                if local_spk not in global_map:
                    global_map[local_spk] = next_global
                    next_global += 1
                result.append({
                    'start': seg['start'],
                    'end': seg['end'],
                    'speaker_global': global_map[local_spk],
                    'duration': seg['duration']
                })
            return result

        logger.info(f"Файл превышает лимит {self.telegram_file_size_limit_mb} МБ, выполняем разбивку на чанки")
        chunks = self._split_audio_into_chunks(audio_file)
        if not chunks:
            logger.error("Не удалось создать чанки для большого файла")
            return None

        chunks_data = []
        for chunk in chunks:
            logger.info(f"Обработка чанка: {chunk['path'].name}")
            segs = self._diarize_single_file(chunk['path'], chunk['start'])
            if segs is None:
                logger.error(f"Не удалось обработать чанк {chunk['path'].name}, прерываем")
                return None
            chunks_data.append({
                'segments': segs,
                'start': chunk['start'],
                'end': chunk['end']
            })

        logger.info("Сопоставление спикеров между чанками...")
        all_segments = self._match_speakers_across_chunks(chunks_data)
        logger.info(f"Сопоставление завершено, всего сегментов: {len(all_segments)}")

        return all_segments

    # ================== МОДИФИЦИРОВАННЫЙ МЕТОД ДИАРИЗАЦИИ ==================

    def process_with_diarization(self, audio_file: Path, base_name: str,
                                is_stereo: bool = False) -> List[Path]:
        """Обработка с использованием диаризации (поддерживает большие файлы)."""
        if not self.pyannote_client or not self.pyannote_available:
            logger.error("❌ Pyannote клиент не доступен")
            return self.fallback_processing(audio_file, base_name)

        if not self.upload_manager:
            logger.error("❌ Telegram upload_manager не доступен")
            return self.fallback_processing(audio_file, base_name)

        try:
            logger.info(f"🎤 ЗАПУСК ДИАРИЗАЦИИ (с поддержкой больших файлов)")

            file_for_diarization = audio_file

            segments_with_global = self._obtain_diarization_segments(file_for_diarization)

            if not segments_with_global:
                logger.error("❌ Не удалось получить результаты диаризации")
                return self.fallback_processing(audio_file, base_name)

            from collections import defaultdict
            speaker_segments = defaultdict(list)
            for seg in segments_with_global:
                speaker_num = seg['speaker_global']
                seg_copy = seg.copy()
                seg_copy['speaker'] = f"SPEAKER_{speaker_num-1:02d}"
                speaker_segments[seg_copy['speaker']].append(seg_copy)

            all_chunks = []
            for speaker, seg_list in speaker_segments.items():
                merged_segments = self._merge_adjacent_segments(
                    seg_list,
                    self.merge_gap_same_speaker
                )

                speaker_chunks = self.create_unified_chunks(
                    audio_file=file_for_diarization,
                    segments=merged_segments,
                    base_name=base_name,
                    method='diarized',
                    source=speaker
                )

                logger.info(f"  👤 {speaker}: {len(speaker_chunks)} чанков")
                all_chunks.extend(speaker_chunks)

            if all_chunks:
                logger.info(f"🎉 Всего создано {len(all_chunks)} чанков через диаризацию")
                return all_chunks
            else:
                logger.error(f"❌ Не удалось создать чанки")
                return self.fallback_processing(audio_file, base_name)

        except Exception as e:
            logger.error(f"❌ Критическая ошибка при диаризации: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return self.fallback_processing(audio_file, base_name)

    def _handle_retry_delay(self, attempt: int):
        """Обработка задержки между повторными попытками"""
        if attempt == 1:
            logger.info("🔄 Первая попытка неудачна - пересоздание API-ключа...")
            if self.renew_pyannote_api_key():
                logger.info("✅ API-ключ пересоздан, ожидание 10 секунд...")
                time.sleep(10)
            else:
                logger.warning("⚠️  Не удалось пересоздать API-ключ, ожидание 10 секунд...")
                time.sleep(10)
        elif attempt == 2:
            logger.info("⏳ Вторая попытка неудачна - ожидание 3 минуты...")
            time.sleep(180)

    def fallback_processing(self, audio_file: Path, base_name: str) -> List[Path]:
        """Fallback-обработка без диаризации"""
        try:
            logger.warning("⚠️  ЗАПУСК FALLBACK-ОБРАБОТКИ БЕЗ ДИАРИЗАЦИИ")
            logger.warning("⚠️  Диаризация недоступна, используем детектирование по паузам")

            quality_info = self.analyze_recording_quality(audio_file)
            strategy = quality_info.get('recommended_strategy', 'standard')

            speech_segments = self.detect_speech_segments(audio_file, quality=strategy)

            if not speech_segments:
                logger.error("❌ Не найдено речевых сегментов")
                return []

            merged_segments = self._merge_adjacent_segments(speech_segments, self.merge_gap_channels)

            chunks = self.create_unified_chunks(
                audio_file=audio_file,
                segments=merged_segments,
                base_name=base_name,
                method='mono',
                source='mono'
            )

            if chunks:
                logger.warning(f"⚠️  Fallback-обработка успешна: создано {len(chunks)} чанков")
                return chunks
            else:
                logger.error("❌ Fallback-обработка не удалась")
                return []

        except Exception as e:
            logger.error(f"❌ Ошибка в fallback-обработке: {e}")
            return []

    def get_input_files(self) -> List[Path]:
        """Получает список файлов для обработки"""
        if not self.input_dir.exists():
            logger.error(f"Папка {self.input_dir} не существует!")
            return []

        input_files = []
        for ext in self.supported_formats:
            input_files.extend(self.input_dir.glob(f"*{ext}"))
            input_files.extend(self.input_dir.glob(f"*{ext.upper()}"))

        unique_files = []
        seen_names = set()

        for file in input_files:
            file_name_lower = file.name.lower()
            if file_name_lower not in seen_names:
                seen_names.add(file_name_lower)
                unique_files.append(file)

        logger.info(f"Найдено файлов для обработки: {len(unique_files)}")
        for file in unique_files[:5]:
            logger.info(f"  • {file.name}")
        if len(unique_files) > 5:
            logger.info(f"  • ... и еще {len(unique_files) - 5} файлов")

        return unique_files

    def process_single_file(self, input_file: Path) -> Dict:
        """Полная обработка одного файла с параллельным созданием контекстных чанков"""
        file_result = {
            'filename': input_file.name,
            'converted': False,
            'channels_found': 0,
            'processing_strategy': 'unknown',
            'chunks_created': 0,
            'context_chunks_created': 0,
            'errors': [],
            'warnings': [],
            'used_fallback': False,
            'context_errors': []
        }

        context_thread = None
        context_result = {}

        try:
            logger.info(f"\n{'='*60}")
            logger.info(f"🎬 ОБРАБОТКА ФАЙЛА: {input_file.name}")
            logger.info(f"{'='*60}")

            logger.info(f"📁 Файл: {input_file.name}")

            if not self.has_audio_stream(input_file):
                error_msg = f"Файл не содержит аудио"
                logger.warning(error_msg)
                file_result['warnings'].append(error_msg)
                file_result['errors'].append("Файл не содержит аудио дорожек")
                return file_result

            converted_file = self.temp_dir / f"{input_file.stem}_converted.wav"
            if not self.convert_to_wav(input_file, converted_file):
                error_msg = "Ошибка конвертации в WAV"
                file_result['errors'].append(error_msg)
                return file_result

            file_result['converted'] = True

            logger.info(f"🎵 ЗАПУСК ПАРАЛЛЕЛЬНОГО СОЗДАНИЯ КОНТЕКСТНЫХ ЧАНКОВ...")

            def context_processing():
                try:
                    mono_file = self.create_mono_for_context(converted_file, input_file.stem)
                    if mono_file and mono_file.exists():
                        result = self.create_context_chunks(mono_file, input_file.stem)
                        context_result.update(result)
                    else:
                        context_result.update({
                            'chunks_created': 0,
                            'errors': ['Не удалось создать моно-версию для контекстных чанков'],
                            'warnings': []
                        })
                except Exception as e:
                    context_result.update({
                        'chunks_created': 0,
                        'errors': [f'Критическая ошибка в потоке контекстных чанков: {str(e)}'],
                        'warnings': []
                    })
                    logger.error(f"❌ Ошибка в потоке контекстных чанков: {e}")

            context_thread = threading.Thread(target=context_processing)
            context_thread.daemon = True
            context_thread.start()
            logger.info(f"✅ Поток для контекстных чанков запущен")

            audio_info = self.get_audio_info(converted_file)
            if audio_info:
                logger.info(f"📊 Аудио информация: {audio_info['channels']} канал(ов), {audio_info['duration']:.1f} сек")

            logger.info(f"🎵 Разделение на каналы...")
            channel_files = self.split_audio_channels(converted_file)

            if not channel_files:
                error_msg = "Не удалось разделить на каналы"
                file_result['errors'].append(error_msg)
                if context_thread and context_thread.is_alive():
                    context_thread.join(timeout=300)
                return file_result

            file_result['channels_found'] = len(channel_files)
            logger.info(f"✅ Разделено на {len(channel_files)} канал(ов)")

            logger.info(f"🔍 Анализ каналов...")
            channel_analysis = self.analyze_channels(channel_files)

            file_result['processing_strategy'] = channel_analysis['recommendation']

            logger.info(f"📋 РЕЗУЛЬТАТЫ АНАЛИЗА:")
            logger.info(f"  • Активных каналов: {channel_analysis['active_channels']}")
            logger.info(f"  • Каналы разные: {'✅ ДА' if channel_analysis.get('is_stereo_different') else '❌ НЕТ'}")
            logger.info(f"  • Рекомендация: {channel_analysis['recommendation'].upper()}")
            logger.info(f"  • Причина: {channel_analysis['reason']}")

            total_chunks = 0

            if channel_analysis['recommendation'] == 'channel_based':
                logger.info(f"⚡ СТРАТЕГИЯ: ОБРАБОТКА ПО КАНАЛАМ")

                chunks = self.process_channel_based(
                    audio_file=converted_file,
                    channel_files=channel_files,
                    channel_names=channel_analysis['channel_names'],
                    base_name=input_file.stem
                )

                total_chunks = len(chunks)
                file_result['processing_method'] = 'channel_based'

            else:
                logger.info(f"⚡ СТРАТЕГИЯ: ДИАРИЗАЦИИ")

                if not self.pyannote_available or not self.upload_manager:
                    error_msg = "Pyannote или Telegram недоступен"
                    logger.error(error_msg)
                    file_result['errors'].append(error_msg)

                    if context_thread and context_thread.is_alive():
                        context_thread.join(timeout=300)
                    return file_result

                is_stereo_file = (audio_info and audio_info['channels'] > 1) if audio_info else False

                if is_stereo_file and not channel_analysis.get('is_stereo_different', False):
                    file_for_diarization = converted_file
                    logger.info("🔊 Стерео файл с одинаковыми каналами - используем для диаризации")
                elif channel_files:
                    file_for_diarization = channel_files[0]
                    logger.info(f"🎵 Используем первый канал для диаризации")
                else:
                    logger.error("❌ Нет подходящего файла для диаризации")
                    file_result['errors'].append("Нет подходящего файла для диаризации")

                    if context_thread and context_thread.is_alive():
                        context_thread.join(timeout=300)
                    return file_result

                try:
                    chunks = self.process_with_diarization(
                        file_for_diarization,
                        input_file.stem,
                        is_stereo=is_stereo_file
                    )

                    total_chunks = len(chunks)
                    file_result['processing_method'] = 'diarization'

                    if chunks and any('mono' in str(chunk) for chunk in chunks):
                        file_result['used_fallback'] = True
                        file_result['warnings'].append("Использован fallback-режим")

                except Exception as e:
                    logger.error(f"❌ Ошибка диаризации: {e}")
                    file_result['errors'].append(f"Ошибка диаризации: {str(e)}")

                    if context_thread and context_thread.is_alive():
                        context_thread.join(timeout=300)
                    return file_result

            file_result['chunks_created'] = total_chunks

            logger.info(f"⏳ Ожидание завершения потока контекстных чанков...")
            if context_thread and context_thread.is_alive():
                context_thread.join(timeout=300)

            if context_result:
                file_result['context_chunks_created'] = context_result.get('chunks_created', 0)
                file_result['context_errors'] = context_result.get('errors', [])
                context_warnings = context_result.get('warnings', [])
                if context_warnings:
                    file_result['warnings'].extend([f"Контекст: {w}" for w in context_warnings])

            logger.info(f"\n📊 РЕЗУЛЬТАТ ОБРАБОТКИ {input_file.name}:")
            logger.info(f"  • Стратегия: {file_result['processing_strategy']}")
            logger.info(f"  • Метод: {file_result.get('processing_method', 'unknown')}")
            logger.info(f"  • Fallback: {'✅ ДА' if file_result['used_fallback'] else '❌ НЕТ'}")
            logger.info(f"  • Чанков создано: {file_result['chunks_created']}")
            logger.info(f"  • Контекстных чанков создано: {file_result['context_chunks_created']}")

            if file_result['warnings']:
                logger.warning(f"  • Предупреждения: {', '.join(file_result['warnings'][:3])}")
                if len(file_result['warnings']) > 3:
                    logger.warning(f"    ... и еще {len(file_result['warnings']) - 3}")

            logger.info(f"{'='*60}")
            logger.info(f"🏁 ЗАВЕРШЕНО: {input_file.name}")
            logger.info(f"{'='*60}\n")

            return file_result

        except Exception as e:
            error_msg = f"Критическая ошибка: {str(e)}"
            logger.error(f"❌ {error_msg}")
            import traceback
            logger.error(traceback.format_exc())
            file_result['errors'].append(error_msg)

            if context_thread and context_thread.is_alive():
                context_thread.join(timeout=30)

            return file_result

    def process_all_files(self) -> Dict:
        """Основной метод для обработки всех файлов"""
        stats = {
            'total_files': 0,
            'successful': 0,
            'failed': 0,
            'total_chunks': 0,
            'total_context_chunks': 0,
            'total_channels': 0,
            'files_with_fallback': 0,
            'errors': [],
            'warnings': []
        }

        try:
            logger.info("\n" + "="*70)
            logger.info("🚀 ЗАПУСК ПАКЕТНОЙ ОБРАБОТКИ")
            logger.info("="*70)

            if not self.check_dependencies():
                stats['errors'].append("Отсутствуют необходимые зависимости")
                return stats

            self.create_temp_directory()

            input_files = self.get_input_files()
            stats['total_files'] = len(input_files)

            if not input_files:
                logger.info("Нет файлов для обработки")
                return stats

            for file in input_files:
                try:
                    result = self.process_single_file(file)

                    if (result['chunks_created'] > 0 or result['context_chunks_created'] > 0) and not result['errors']:
                        stats['successful'] += 1
                        stats['total_chunks'] += result['chunks_created']
                        stats['total_context_chunks'] += result['context_chunks_created']
                        stats['total_channels'] += result['channels_found']

                        if result.get('used_fallback', False):
                            stats['files_with_fallback'] += 1
                            logger.warning(f"⚠️  {file.name}: {result['chunks_created']} чанков + {result['context_chunks_created']} контекстных (FALLBACK)")
                        else:
                            logger.info(f"✅ {file.name}: {result['chunks_created']} чанков + {result['context_chunks_created']} контекстных")
                    else:
                        stats['failed'] += 1
                        stats['errors'].extend(result['errors'])
                        stats['errors'].extend([f"Контекст: {e}" for e in result.get('context_errors', [])])
                        stats['warnings'].extend(result.get('warnings', []))
                        if result['errors'] or result.get('context_errors'):
                            error_count = len(result['errors']) + len(result.get('context_errors', []))
                            logger.error(f"❌ {file.name}: {error_count} ошибок")
                        else:
                            logger.warning(f"⚠️  {file.name}: не создано чанков")

                except Exception as e:
                    stats['failed'] += 1
                    error_msg = f"{file.name}: {str(e)}"
                    stats['errors'].append(error_msg)
                    logger.error(f"❌ Ошибка при обработке {file.name}: {e}")

            self.cleanup_temp_directory()

            logger.info("\n" + "="*70)
            logger.info("📊 ИТОГОВАЯ СТАТИСТИКА")
            logger.info("="*70)
            logger.info(f"Всего файлов: {stats['total_files']}")
            logger.info(f"Успешно: {stats['successful']}")
            logger.info(f"С ошибками: {stats['failed']}")
            logger.info(f"С fallback: {stats['files_with_fallback']}")
            logger.info(f"Всего каналов: {stats['total_channels']}")
            logger.info(f"Всего чанков: {stats['total_chunks']}")
            logger.info(f"Всего контекстных чанков: {stats['total_context_chunks']}")

            if stats['files_with_fallback'] > 0:
                logger.warning(f"\n⚠️  ВНИМАНИЕ: {stats['files_with_fallback']} файл(ов) обработаны в fallback-режиме!")
                logger.warning("   Качество может быть снижено - возможны ошибки с определением спикеров")

            if stats['warnings']:
                logger.warning(f"\n⚠️  Предупреждения: {len(stats['warnings'])}")
                for warning in stats['warnings'][:3]:
                    logger.warning(f"  • {warning}")
                if len(stats['warnings']) > 3:
                    logger.warning(f"  • ... и еще {len(stats['warnings']) - 3}")

            if stats['errors']:
                logger.error(f"\n❌ Ошибки: {len(stats['errors'])}")
                for error in stats['errors'][:5]:
                    logger.error(f"  • {error}")
                if len(stats['errors']) > 5:
                    logger.error(f"  • ... и еще {len(stats['errors']) - 5}")

            logger.info(f"\n📁 Основные чанки сохранены в: {self.chunk_files_dir}")
            logger.info(f"📁 Контекстные чанки сохранены в: {self.context_files_dir}")
            logger.info("="*70)

            return stats

        except Exception as e:
            logger.error(f"❌ Критическая ошибка: {e}")
            stats['errors'].append(f"Критическая ошибка: {str(e)}")

            try:
                self.cleanup_temp_directory()
            except:
                pass

            return stats


def main():
    """Основная функция для запуска модуля"""
    try:
        logger.info("🚀 ЗАПУСК УНИФИЦИРОВАННОГО PROCESSOR (С ПОДДЕРЖКОЙ БОЛЬШИХ ФАЙЛОВ)")

        processor = AudioProcessorUnified()
        stats = processor.process_all_files()

        if stats['successful'] > 0:
            if stats['files_with_fallback'] > 0:
                logger.warning("\n⚠️  ОБРАБОТКА ЗАВЕРШЕНА С ЧАСТИЧНЫМ УСПЕХОМ!")
                logger.warning(f"   {stats['files_with_fallback']} файл(ов) в fallback-режиме")
            else:
                logger.info("\n✅ ОБРАБОТКА ЗАВЕРШЕНА УСПЕШНО!")
            return 0
        else:
            logger.error("\n❌ ОБРАБОТКА ЗАВЕРШЕНА С ОШИБКАМИ")
            return 1

    except KeyboardInterrupt:
        logger.warning("\n⚠️  ОБРАБОТКА ПРЕРВАНА ПОЛЬЗОВАТЕЛЕМ")
        return 130
    except Exception as e:
        logger.error(f"\n❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return 2


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)