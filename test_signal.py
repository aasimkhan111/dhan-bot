import requests

# This is the address of your bot running on your laptop
URL = "http://127.0.0.1:5000/webhook"

# This mimics the JSON TradingView would send
payload = {
    "secret": "JunnarTrader2026",
    "symbol": "BANKNIFTY-Jun2026-65400-CE",  
    "side": "buy",
    "quantity": 30, # (June 2026 Lot Size is 30)
    "order_type": "LIMIT", # Tell the bot it's a Limit Order
    "price": 0.10          # Place a dummy limit order price
}



response = requests.post(URL, json=payload)
print(f"Status Code: {response.status_code}")
print(f"Response from Bot: {response.json()}")