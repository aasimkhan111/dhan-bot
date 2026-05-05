import pandas as pd
from flask import Flask, request, jsonify
from dhanhq import dhanhq, DhanContext

app = Flask(__name__)

# --- LIVE CONFIG ---
CLIENT_ID = "1100819221"
ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc4MDQwMDUzLCJpYXQiOjE3Nzc5NTM2NTMsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAwODE5MjIxIn0.G7AewK4Ex-XsNwIucC7aoivUbNVqmZNOvC_RmUIxYp9QjIEQzyWDRyOAt-LWiIFIr1nYi6IhRB4j1Jo_3hPjjw"
SECRET_TOKEN = "JunnarTrader2026"

# Connect to Dhan
dhan_context = DhanContext(client_id=CLIENT_ID, access_token=ACCESS_TOKEN)
dhan = dhanhq(dhan_context)

print("Syncing with Dhan Master Scrip List...")
SCRIP_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
df = pd.read_csv(SCRIP_URL, low_memory=False)

def get_security_id(symbol, price=0, option_type=None):
    try:
        symbol = symbol.upper()
        if "-ATM" in symbol or "-ITM" in symbol:
            base = symbol.split("-ATM")[0].split("-ITM")[0]
            step = 100 if "BANKNIFTY" in base else 50
            strike = round(price / step) * step
            
            # Simple ATM logic for the user's specific script
            mask = (df['SEM_INSTRUMENT_NAME'] == 'OPTIDX') & \
                   (df['SEM_TRADING_SYMBOL'].str.contains(base)) & \
                   (df['SEM_STRIKE_PRICE'] == strike) & \
                   (df['SEM_OPTION_TYPE'] == option_type)
            
            res = df[mask].sort_values(by='SEM_EXPIRY_DATE')
            if not res.empty:
                return res.iloc[0]['SEM_SMART_SYMBOL'], res.iloc[0]['SEM_EXCHANGE_SEGMENT']
        
        # Direct lookup
        res = df[df['SEM_TRADING_SYMBOL'] == symbol]
        if not res.empty:
            return res.iloc[0]['SEM_SMART_SYMBOL'], res.iloc[0]['SEM_EXCHANGE_SEGMENT']
            
        return None, None
    except:
        return None, None

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        print(f"📥 Received Webhook: {data}")

        if data.get('secret') != SECRET_TOKEN:
            return jsonify({"error": "Invalid Secret"}), 403

        # --- SPECIAL CASE: EXIT ALL POSITIONS ---
        if data.get('action') == 'exit':
            print("🛑 Exit signal received! Squaring off all positions...")
            pos_res = dhan.get_positions()
            if pos_res.get('status') == 'success':
                positions = pos_res.get('data', [])
                for pos in positions:
                    qty = int(pos.get('netQty', 0))
                    if qty != 0:
                        print(f"Closing {pos.get('tradingSymbol')} | Qty: {qty}")
                        dhan.place_order(
                            security_id=pos.get('securityId'),
                            exchange_segment=pos.get('exchangeSegment'),
                            transaction_type=dhan.SELL if qty > 0 else dhan.BUY,
                            quantity=abs(qty),
                            order_type=dhan.MARKET,
                            product_type=pos.get('productType'),
                            after_market_order=False
                        )
                return jsonify({"status": "success", "message": "All positions closed"}), 200
            return jsonify({"error": "Could not fetch positions"}), 500

        # --- NORMAL ORDER LOGIC ---
        symbol = data.get('symbol')
        if not symbol:
            return jsonify({"error": "No symbol provided"}), 400
            
        side = data.get('side', 'buy').upper()
        price = float(data.get('price', 0))
        opt_type = data.get('option_type', 'CE').upper()

        sec_id, exch_seg = get_security_id(symbol, price, opt_type)
        
        if not sec_id:
            return jsonify({"error": f"Symbol {symbol} not found"}), 404

        response = dhan.place_order(
            security_id=int(sec_id),
            exchange_segment=exch_seg,
            transaction_type=dhan.BUY if side == 'BUY' else dhan.SELL,
            quantity=int(data.get('quantity', 0)),
            order_type=dhan.MARKET,
            product_type=dhan.INTRA,
            after_market_order=False
        )
        
        print(f"📡 Order Response: {response}")
        return jsonify(response), 200

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
