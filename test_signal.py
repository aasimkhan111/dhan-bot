import requests

# 🚨 YAHAN APNE NAYE EC2 SERVER KA IP DALO 🚨
EC2_IP = "43.205.136.79"  # <-- Ise change karo

URL = f"http://{EC2_IP}:80/webhook"

# Fake TradingView Alert (BankNifty ITM CE)
payload = {
    "secret": "JunnarTrader2026",
    "symbol": "BANKNIFTY-ITM",  
    "side": "buy",
    "quantity": 60,
    "order_type": "MARKET",
    "price": 56000.0,  # Fake BankNifty Price
    "option_type": "CE"
}

print(f"Sending fake signal to {URL}...")
try:
    response = requests.post(URL, json=payload, timeout=10)
    print(f"Status Code: {response.status_code}")
    print(f"Response from EC2 Bot: {response.json()}")
except Exception as e:
    print(f"Error connecting to server: {e}")
