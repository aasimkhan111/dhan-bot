import os
import json
import subprocess
import traceback
import datetime
import time
import uuid
import csv
import pandas as pd
from flask import Flask, request, jsonify, render_template_string
from dhanhq import dhanhq, DhanContext

app = Flask(__name__)

CONFIG_FILE = "config.json"
SCRIP_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

# --- HELPER FUNCTIONS ---
def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Error loading config.json: {e}")
    
    # Fallback default values
    return {
        "CLIENT_ID": "1100819221",
        "ACCESS_TOKEN": "",
        "SECRET_TOKEN": "JunnarTrader2026"
    }

EC2_IP = "43.205.136.79"

def run_and_log_command(cmd_args, cwd=None):
    """Execute a shell command, log its output, and return (success, output_text)."""
    try:
        cmd_str = ' '.join(cmd_args)
        print(f"💻 Executing: {cmd_str}")
        result = subprocess.run(cmd_args, cwd=cwd, capture_output=True, text=True, check=True)
        output = ""
        if result.stdout:
            output += result.stdout.strip()
            print(f"   ↳ {result.stdout.strip()}")
        if result.stderr:
            output += result.stderr.strip()
            print(f"   ↳ {result.stderr.strip()}")
        return True, output
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed: {' '.join(cmd_args)}")
        err_out = ""
        if e.stdout:
            err_out += e.stdout.strip()
            print(f"   ↳ {e.stdout.strip()}")
        if e.stderr:
            err_out += e.stderr.strip()
            print(f"   ↳ {e.stderr.strip()}")
        return False, err_out
    except Exception as ex:
        print(f"❌ Execution error: {ex}")
        return False, str(ex)

def save_and_deploy(config_data):
    """Save config, push to GitHub, and sync EC2. Returns pipeline steps."""
    steps = []
    
    # ── STEP 1: Save config.json locally ──
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config_data, f, indent=4)
        print("💾 [Step 1/4] Configuration saved to config.json")
        steps.append({"step": "Save config.json", "status": "success", "detail": "Credentials written to disk"})
    except Exception as e:
        print(f"❌ [Step 1/4] Failed to save config.json: {e}")
        steps.append({"step": "Save config.json", "status": "failed", "detail": str(e)})
        return steps  # Cannot continue without saving
    
    # ── On EC2 (Linux): Skip git ops, config is already live ──
    if os.name != 'nt':
        print("✅ [Step 2/2] Running on EC2 — config saved and applied instantly!")
        steps.append({"step": "EC2 Live Reload", "status": "success", "detail": "Config applied instantly on server (no git needed)"})
        return steps
    
    # ── STEP 2: Git Add + Commit (LOCAL Windows only) ──
    if os.path.exists(".git"):
        print("📦 [Step 2/4] Staging and committing changes...")
        ok_add, _ = run_and_log_command(["git", "add", "config.json"])
        ok_commit, commit_out = run_and_log_command(["git", "commit", "-m", "Auto-update credentials via Admin Dashboard"])
        if ok_add and ok_commit:
            steps.append({"step": "Git Commit", "status": "success", "detail": commit_out or "Changes committed"})
        else:
            steps.append({"step": "Git Commit", "status": "warning", "detail": commit_out or "Nothing new to commit (token may be same)"})
    else:
        steps.append({"step": "Git Commit", "status": "skipped", "detail": "Not a git repository"})
        return steps
    
    # ── STEP 3: Git Push to GitHub ──
    print("🚀 [Step 3/4] Pushing to GitHub...")
    ok_push, push_out = run_and_log_command(["git", "push"])
    if ok_push:
        steps.append({"step": "Git Push", "status": "success", "detail": push_out or "Pushed to origin/main"})
    else:
        steps.append({"step": "Git Push", "status": "failed", "detail": push_out or "Push failed"})
        return steps
    
    # ── STEP 4: Trigger EC2 to pull latest ──
    print(f"🌐 [Step 4/4] Triggering Git Pull on EC2 ({EC2_IP})...")
    try:
        import requests as req_lib
        url = f"http://{EC2_IP}/api/git-pull-and-reload"
        payload = {"secret": config_data.get('SECRET_TOKEN', 'JunnarTrader2026')}
        resp = req_lib.post(url, json=payload, timeout=20)
        if resp.status_code == 200:
            print("🎉 [Step 4/4] EC2 Server pulled latest changes and reloaded!")
            steps.append({"step": "EC2 Auto-Pull", "status": "success", "detail": "Server reloaded with new credentials"})
        else:
            print(f"⚠️ [Step 4/4] EC2 sync returned: {resp.text}")
            steps.append({"step": "EC2 Auto-Pull", "status": "failed", "detail": resp.text})
    except Exception as e:
        print(f"⚠️ [Step 4/4] Could not contact EC2: {e}")
        steps.append({"step": "EC2 Auto-Pull", "status": "failed", "detail": str(e)})
    
    return steps

# In-Memory sync with Dhan Master Scrip List
df = None
last_scrip_load_date = None

def get_ist_now():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)

def load_scrip_master():
    global df, last_scrip_load_date
    today_ist = get_ist_now().date()
    
    if df is not None and last_scrip_load_date == today_ist:
        return
        
    print(f"[Strictly In-Memory] Syncing with Dhan Master Scrip List for {today_ist}...")
    try:
        df = pd.read_csv(SCRIP_URL, low_memory=False)
        last_scrip_load_date = today_ist
        print(f"Dhan Scrip Master loaded in memory. Size: {len(df)} rows.")
    except Exception as e:
        print(f"Error loading Dhan Scrip Master directly from URL: {e}")
        if df is not None:
            print("Warning: Keeping existing in-memory Scrip Master as fallback.")
        else:
            raise e

# Initial load on startup
try:
    load_scrip_master()
except Exception as e:
    print(f"Warning: Direct startup scrip load failed (will retry on webhook): {e}")

def get_security_id(symbol, price=0, option_type=None, manual_strike=None):
    try:
        load_scrip_master() # Ensure in-memory dataframe is fresh for today
        symbol = symbol.upper()
        # --- DYNAMIC ATM/ITM LOGIC ---
        if "-ATM" in symbol or "-ITM" in symbol:
            base = symbol.split("-ATM")[0].split("-ITM")[0]
            
            # Use manual strike if provided (for precise exits), else calculate from price
            if manual_strike:
                strike = float(manual_strike)
            else:
                if price == 0:
                    print(f"Error: price=0 received for {symbol} request.")
                    return None, None, None, None
                # Calculate Strike (Step of 100 for BankNifty, 50 for Nifty)
                step = 100 if "BANKNIFTY" in base else 50
                strike = round(price / step) * step
                
                # Apply ITM Offset (100 points for BankNifty, 50 for Nifty)
                if "-ITM" in symbol:
                    multiplier = 1
                    try:
                        suffix = symbol.split("-ITM")[1]
                        if suffix.isdigit():
                            multiplier = int(suffix)
                    except:
                        pass
                        
                    if option_type == 'CE': strike -= (step * multiplier)
                    else: strike += (step * multiplier)

            print(f"[ATM/ITM] Using {symbol} logic for {base}: Strike {strike}")
            
            # Find the option with this strike and nearest expiry
            match = df[(df['SEM_INSTRUMENT_NAME'] == 'OPTIDX') & 
                       (df['SEM_STRIKE_PRICE'].astype(float) == float(strike)) &
                       (df['SEM_OPTION_TYPE'] == option_type) &
                       ((df['SM_SYMBOL_NAME'].str.contains(base, case=False, na=False)) | 
                        (df['SEM_CUSTOM_SYMBOL'].str.contains(base, case=False, na=False)) |
                        (df['SEM_TRADING_SYMBOL'].str.contains(base, case=False, na=False)))]
            
            if match.empty:
                print(f"Warning: No strike {strike} found for {base}.")
                return None, None, None, None
            
        elif symbol.endswith('-I'):
            base = symbol.replace('-I', '')
            match = df[(df['SEM_INSTRUMENT_NAME'].isin(['FUTIDX', 'FUTSTK'])) & 
                       (df['SEM_TRADING_SYMBOL'].str.startswith(base)) &
                       (df['SEM_EXM_EXCH_ID'] == 'NSE')]
        else:
            # Regular exact match
            match = df[(df['SEM_TRADING_SYMBOL'].str.upper() == symbol) & (df['SEM_EXM_EXCH_ID'].isin(['NSE', 'NFO']))]
            if match.empty:
                match = df[(df['SEM_CUSTOM_SYMBOL'].str.upper() == symbol) & (df['SEM_EXM_EXCH_ID'].isin(['NSE', 'NFO']))]

        if not match.empty:
            # Sort by expiry to get the nearest one safely
            match = match.copy()
            if 'SEM_EXPIRY_DATE' in match.columns:
                match['expiry_dt'] = pd.to_datetime(match['SEM_EXPIRY_DATE'], errors='coerce')
                # Filter out expired contracts (expiry date before today)
                today = pd.Timestamp.now().normalize()
                match = match[match['expiry_dt'] >= today]
                match = match.dropna(subset=['expiry_dt']).sort_values('expiry_dt')
            
            if match.empty:
                print(f"Warning: No ACTIVE (unexpired) strike {symbol} found.")
                return None, None, None, None

            # Use the first active match
            row = match.iloc[0]
            sec_id = int(row['SEM_SMST_SECURITY_ID'])
            inst_name = str(row['SEM_INSTRUMENT_NAME'])
            final_symbol = str(row['SEM_TRADING_SYMBOL'])
            expiry = str(row['SEM_EXPIRY_DATE'])
            strike = float(row.get('SEM_STRIKE_PRICE', 0))
            base_symbol = str(row.get('SEM_CUSTOM_SYMBOL', '') or row.get('SM_SYMBOL_NAME', '') or symbol.split("-")[0])
            
            print(f"Found: {final_symbol} | ID: {sec_id} | Expiry: {expiry} | Strike: {strike} | Base: {base_symbol}")
            return sec_id, inst_name, strike, base_symbol
        else:
            print(f"Symbol {symbol} not found.")
            return None, None, None, None
    except Exception as e:
        print(f"Lookup Error: {e}")
        return None, None, None, None

# --- CSV TRADE JOURNAL UTILITIES ---
JOURNAL_FILE = "trade_journal.csv"

def init_journal():
    if not os.path.exists(JOURNAL_FILE):
        try:
            with open(JOURNAL_FILE, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    "trade_id", "symbol", "option_type", "strike", "quantity",
                    "buy_time", "buy_price", "sell_time", "sell_price", "p_l",
                    "status", "buy_order_id", "sell_order_id", "remarks"
                ])
            print("Initialized fresh trade_journal.csv")
        except Exception as e:
            print(f"Error initializing trade_journal.csv: {e}")

def get_all_trades():
    init_journal()
    trades = []
    if os.path.exists(JOURNAL_FILE):
        try:
            with open(JOURNAL_FILE, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    trades.append(row)
        except Exception as e:
            print(f"Error reading trade_journal.csv: {e}")
    return trades

def save_all_trades(trades):
    try:
        with open(JOURNAL_FILE, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                "trade_id", "symbol", "option_type", "strike", "quantity",
                "buy_time", "buy_price", "sell_time", "sell_price", "p_l",
                "status", "buy_order_id", "sell_order_id", "remarks"
            ])
            for t in trades:
                writer.writerow([
                    t.get("trade_id", ""),
                    t.get("symbol", ""),
                    t.get("option_type", ""),
                    t.get("strike", ""),
                    t.get("quantity", ""),
                    t.get("buy_time", ""),
                    t.get("buy_price", ""),
                    t.get("sell_time", ""),
                    t.get("sell_price", ""),
                    t.get("p_l", ""),
                    t.get("status", ""),
                    t.get("buy_order_id", ""),
                    t.get("sell_order_id", ""),
                    t.get("remarks", "")
                ])
    except Exception as e:
        print(f"Error saving trade_journal.csv: {e}")

# --- API ROUTES ---


import threading

def _process_order_async(data, config):
    """Background worker: resolves strike, places order, polls status, logs trade.
    Runs in a daemon thread so TradingView gets an instant 200 OK."""
    try:
        symbol = data.get('symbol')
        price = float(data.get('price', 0))
        option_type = data.get('option_type', 'CE').upper()
        manual_strike = data.get('itm_strike')
        
        order_type_str = data.get('order_type', 'MARKET').upper()
        side_str = data.get('side', 'BUY').upper()

        # === SELL STRIKE AUTO-MATCH ===
        # For SELL orders: If no itm_strike provided, auto-match from trade journal
        # so we sell the EXACT same option we bought (not a new ATM strike)
        if side_str == 'SELL' and not manual_strike:
            print(f"[SELL Auto-Match] No itm_strike in webhook. Searching trade journal for open {option_type} position...")
            open_trades = get_all_trades()
            matched_open = None
            for t in reversed(open_trades):
                if t.get('option_type') == option_type and t.get('status', '').startswith('OPEN'):
                    matched_open = t
                    break
            
            if matched_open:
                journal_strike = matched_open.get('strike', '')
                if journal_strike:
                    manual_strike = journal_strike
                    print(f"[SELL Auto-Match] ✅ Found open {option_type} position with Strike: {manual_strike} (Trade ID: {matched_open.get('trade_id')})")
                else:
                    print(f"[SELL Auto-Match] ⚠️ Open trade found but no strike recorded. Falling back to dynamic calculation.")
            else:
                print(f"[SELL Auto-Match] ⚠️ No open {option_type} position in journal. Using dynamic strike calculation.")

        # Resolve exact Security ID, Instrument Type, Strike, and Base Symbol
        sec_id, inst_name, strike, base_symbol = get_security_id(symbol, price, option_type, manual_strike)
        
        if not sec_id:
            print(f"[BG] Webhook resolution failed: Symbol {symbol} not found.")
            return

        # Initialize Dhan client dynamically to always use latest credentials
        dhan_context = DhanContext(client_id=config['CLIENT_ID'], access_token=config['ACCESS_TOKEN'])
        dhan_live = dhanhq(dhan_context)
        
        # FORCE CORRECT SEGMENT
        if inst_name in ['OPTIDX', 'OPTSTK', 'FUTIDX', 'FUTSTK']:
            exch_seg = dhan_live.NSE_FNO
        else:
            exch_seg = dhan_live.NSE

        dhan_order_type = dhan_live.MARKET
        final_price = 0.0
        
        # --- Fetch exact LTP for precise limit orders or simulated fallback ---
        print(f"Fetching Precise Option LTP for ID: {sec_id} via Data API...")
        option_ltp = 0.0
        try:
            seg_key = "NSE_FNO" if exch_seg == dhan_live.NSE_FNO else "NSE"
            securities = {seg_key: [int(sec_id)]}
            quote = dhan_live.ticker_data(securities)
            print(f"Raw Ticker Response: {quote}")
            if isinstance(quote, dict) and quote.get('status') == 'success':
                outer_data = quote.get('data', {})
                inner_data = outer_data.get('data', {})
                seg_data = inner_data.get(seg_key, {})
                id_data = seg_data.get(str(sec_id), {})
                ltp = float(id_data.get('last_price', 0))
                if ltp > 0:
                    option_ltp = ltp
                    print(f"Exact Option LTP: {option_ltp}")
        except Exception as e:
            print(f"Error fetching option LTP: {e}")

        # === BUY ORDERS: Use exact LTP via LIMIT order ===
        if side_str == 'BUY':
            if option_ltp > 0:
                final_price = option_ltp
                dhan_order_type = dhan_live.LIMIT
                print(f"Precise Entry: Using LIMIT order at exact LTP: {final_price}")
            else:
                dhan_order_type = dhan_live.MARKET
                print("Warning: LTP not available. Falling back to MARKET for entry.")
        # === SELL ORDERS: Always use MARKET for guaranteed exit ===
        else:
            print(f"EXIT detected — Using MARKET order for guaranteed exit.")
            dhan_order_type = dhan_live.MARKET
            final_price = 0.0
        
        quantity_val = int(data.get('quantity', 30))
        print(f"Firing {'MARKET' if dhan_order_type == dhan_live.MARKET else 'LIMIT'} order | ID: {sec_id} | Side: {side_str} | Price: {final_price} | Qty: {quantity_val}")

        response = {}
        order_placed = False
        error_msg = ""
        
        try:
            # Place order on Dhan API
            response = dhan_live.place_order(
                security_id=int(sec_id),
                exchange_segment=exch_seg,
                transaction_type=dhan_live.BUY if side_str == 'BUY' else dhan_live.SELL,
                quantity=quantity_val,
                order_type=dhan_order_type,
                product_type=dhan_live.MARGIN, 
                price=float(final_price),
                after_market_order=False 
            )
            print(f"Dhan API Order Response: {response}")
            if isinstance(response, dict) and response.get('status') == 'success':
                order_placed = True
            else:
                error_msg = response.get('remarks', {}).get('error_message') or response.get('remarks') or "Dhan rejection"
        except Exception as ex:
            error_msg = str(ex)
            print(f"Dhan API place_order exception: {ex}")

        # --- POLL ORDER STATUS ---
        order_id = ""
        order_status = "REJECTED"
        avg_price = 0.0
        
        if order_placed:
            order_id = response.get('data', {}).get('orderId', '')
            if order_id:
                print(f"Polling Dhan API for status of Order ID {order_id}...")
                for attempt in range(5):
                    time.sleep(0.5)
                    try:
                        order_desc = dhan_live.get_order_by_id(order_id)
                        if order_desc.get('status') == 'success':
                            order_data = order_desc.get('data', {})
                            status_check = order_data.get('orderStatus', '')
                            if status_check:
                                order_status = status_check
                                avg_price = float(order_data.get('averageTradedPrice', 0))
                                error_msg = order_data.get('rejectReason', '') or error_msg
                                print(f"   -> Attempt {attempt+1}: Status={order_status} | Price={avg_price}")
                                if order_status in ['TRADED', 'REJECTED', 'CANCELLED']:
                                    break
                    except Exception as pe:
                        print(f"   Warning: Polling attempt {attempt+1} error: {pe}")
        else:
            order_status = "REJECTED"
            
        print(f"Final Order Status: {order_status} | Executed Avg Price: {avg_price} | Rejection Reason: {error_msg}")

        # --- TRADE JOURNAL LOGGING & P&L MATCHING ---
        trades = get_all_trades()
        now_str = get_ist_now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Determine if this is a real or simulated trade (e.g. no funds)
        is_simulated = (order_status == "REJECTED" or not order_placed)
        
        # Final price to log: executed average or fallback to live LTP or TradingView price
        trade_price = avg_price
        if trade_price == 0.0:
            trade_price = option_ltp if option_ltp > 0.0 else price
            
        if side_str == 'BUY':
            # Create a new trade entry
            trade_id = str(uuid.uuid4())[:8]
            status_str = "OPEN" if not is_simulated else "OPEN (Simulated)"
            remarks_str = f"Simulated: {error_msg}" if is_simulated else "Real Trade Opened"
            
            new_trade = {
                "trade_id": trade_id,
                "symbol": str(base_symbol),
                "option_type": str(option_type),
                "strike": str(strike),
                "quantity": str(quantity_val),
                "buy_time": now_str,
                "buy_price": str(round(trade_price, 2)),
                "sell_time": "",
                "sell_price": "",
                "p_l": "0.0",
                "status": status_str,
                "buy_order_id": order_id if not is_simulated else "SIMULATED",
                "sell_order_id": "",
                "remarks": remarks_str
            }
            trades.append(new_trade)
            print(f"Logged Entry Trade: {trade_id} | Strike: {strike} | Price: {trade_price} | Status: {status_str}")
            save_all_trades(trades)
            
        elif side_str == 'SELL':
            # Match with the latest open trade of same option type (CE or PE)
            matched_trade = None
            for t in reversed(trades):
                status_check = t.get('status', '')
                if t.get('option_type') == option_type and status_check.startswith('OPEN'):
                    matched_trade = t
                    break
                    
            if matched_trade:
                buy_p = float(matched_trade.get('buy_price', 0) or 0)
                p_l_val = (trade_price - buy_p) * quantity_val
                
                # If either entry or exit was simulated, the whole trade is closed as simulated
                entry_was_sim = matched_trade.get('status', '') == "OPEN (Simulated)" or matched_trade.get('buy_order_id') == "SIMULATED"
                exit_was_sim = is_simulated
                
                status_str = "CLOSED" if (not entry_was_sim and not exit_was_sim) else "CLOSED (Simulated)"
                remarks_str = ""
                if entry_was_sim: remarks_str += "Sim Entry"
                if exit_was_sim: remarks_str += (" & " if remarks_str else "") + f"Sim Exit ({error_msg})"
                if not remarks_str: remarks_str = "Real Trade Closed"
                
                matched_trade['sell_time'] = now_str
                matched_trade['sell_price'] = str(round(trade_price, 2))
                matched_trade['p_l'] = str(round(p_l_val, 2))
                matched_trade['status'] = status_str
                matched_trade['sell_order_id'] = order_id if not exit_was_sim else "SIMULATED"
                matched_trade['remarks'] = remarks_str
                
                print(f"Logged Exit Trade: {matched_trade['trade_id']} | Strike: {strike} | Price: {trade_price} | P&L: {p_l_val} | Status: {status_str}")
                save_all_trades(trades)
            else:
                # Orphan exit
                trade_id = str(uuid.uuid4())[:8]
                status_str = "REJECTED" if is_simulated else "ORPHAN_EXIT"
                remarks_str = f"Orphan Exit (No open {option_type} position found)"
                if is_simulated:
                    remarks_str += f" | Rejection: {error_msg}"
                    
                orphan_trade = {
                    "trade_id": trade_id,
                    "symbol": str(base_symbol),
                    "option_type": str(option_type),
                    "strike": str(strike),
                    "quantity": str(quantity_val),
                    "buy_time": "",
                    "buy_price": "",
                    "sell_time": now_str,
                    "sell_price": str(round(trade_price, 2)),
                    "p_l": "0.0",
                    "status": status_str,
                    "buy_order_id": "",
                    "sell_order_id": order_id if not is_simulated else "SIMULATED",
                    "remarks": remarks_str
                }
                trades.append(orphan_trade)
                print(f"Warning: Logged Orphan Exit: {trade_id} | Strike: {strike} | Price: {trade_price}")
                save_all_trades(trades)

        print(f"[BG] ✅ Order processing complete for {side_str} {symbol}")

    except Exception as e:
        error_details = traceback.format_exc()
        print(f"[BG] Order Execution Error:\n{error_details}")


@app.route('/webhook', methods=['POST'])
def webhook():
    """Instant webhook handler — validates and responds in <500ms.
    All heavy processing (scrip lookup, order, polling) runs in background thread."""
    data = request.get_json(force=True, silent=True)
    config = load_config()
    
    if not data or data.get('secret') != config.get('SECRET_TOKEN', 'JunnarTrader2026'):
        print(f"403 ERROR! Unauthorized request.")
        return jsonify({"status": "error", "remarks": "Unauthorized"}), 403

    # Log what we received for debugging
    side_str = data.get('side', 'BUY').upper()
    symbol = data.get('symbol', '?')
    print(f"⚡ Webhook received: {side_str} {symbol} — dispatching to background thread...")

    # Fire-and-forget: launch order processing in a daemon background thread
    worker = threading.Thread(target=_process_order_async, args=(data, config), daemon=True)
    worker.start()

    # Return 200 OK INSTANTLY to TradingView (< 100ms)
    return jsonify({
        "status": "received",
        "remarks": f"{side_str} {symbol} accepted — processing in background"
    }), 200

# --- FRONTEND ROUTE & API ---

@app.route('/')
def admin_dashboard():
    # Premium glassmorphic interface
    html_template = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Regal Algo | Duocore Softwares</title>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Fira+Code:wght@400;500&family=Inter:wght@300;400;600&display=swap" rel="stylesheet">
        <style>
            :root {
                --bg-deep: #03050a;
                --card-glass: rgba(13, 17, 28, 0.7);
                --card-border: rgba(255, 255, 255, 0.05);
                --border-glow: rgba(121, 40, 202, 0.3);
                --primary: #7928CA;
                --secondary: #00DFD8;
                --accent: #FF007A;
                --text-main: #f8fafc;
                --text-dim: #94a3b8;
                --success: #10b981;
                --error: #ef4444;
                --panel-shadow: 0 20px 50px rgba(0, 0, 0, 0.6);
            }

            * {
                box-sizing: border-box;
                margin: 0;
                padding: 0;
                cursor: default;
            }

            body {
                background-color: var(--bg-deep);
                background-image: 
                    radial-gradient(circle at 20% 30%, rgba(121, 40, 202, 0.15) 0%, transparent 40%),
                    radial-gradient(circle at 80% 70%, rgba(0, 223, 216, 0.1) 0%, transparent 40%),
                    linear-gradient(to bottom, transparent, rgba(0,0,0,0.5));
                font-family: 'Inter', sans-serif;
                color: var(--text-main);
                min-height: 100vh;
                display: flex;
                flex-direction: column;
                align-items: center;
                padding: 3rem 1.5rem;
                overflow-x: hidden;
                position: relative;
            }

            /* Animated Background Particles */
            body::before {
                content: '';
                position: fixed;
                top: 0; left: 0; width: 100%; height: 100%;
                background: url('https://www.transparenttextures.com/patterns/stardust.png');
                opacity: 0.2;
                z-index: -2;
                pointer-events: none;
            }

            header {
                text-align: center;
                margin-bottom: 4rem;
                z-index: 10;
                animation: fadeInDown 1s ease-out;
            }

            @keyframes fadeInDown {
                from { opacity: 0; transform: translateY(-20px); }
                to { opacity: 1; transform: translateY(0); }
            }

            .logo-container {
                display: flex;
                align-items: baseline;
                justify-content: center;
                gap: 1.2rem;
                flex-wrap: wrap;
            }

            header h1 {
                font-family: 'Outfit', sans-serif;
                font-size: 3.5rem;
                font-weight: 800;
                letter-spacing: -0.06em;
                background: linear-gradient(135deg, #fff 20%, var(--secondary) 50%, var(--primary) 100%);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                margin-bottom: 0;
                filter: drop-shadow(0 0 20px rgba(121, 40, 202, 0.3));
            }

            header p {
                font-family: 'Outfit', sans-serif;
                color: var(--text-dim);
                font-size: 1rem;
                font-weight: 400;
                letter-spacing: 0.2em;
                text-transform: uppercase;
                opacity: 0.8;
                margin-bottom: 0.6rem;
            }

            .status-container {
                margin-top: 1.5rem;
            }

            .status-badge {
                display: inline-flex;
                align-items: center;
                gap: 0.6rem;
                background: rgba(16, 185, 129, 0.05);
                border: 1px solid rgba(16, 185, 129, 0.2);
                padding: 0.5rem 1.2rem;
                border-radius: 100px;
                font-size: 0.85rem;
                color: var(--success);
                font-weight: 600;
                letter-spacing: 0.05em;
                box-shadow: 0 0 20px rgba(16, 185, 129, 0.1);
            }

            .status-badge::before {
                content: '';
                width: 10px;
                height: 10px;
                background: var(--success);
                border-radius: 50%;
                box-shadow: 0 0 12px var(--success);
                animation: pulseGlow 2s infinite;
            }

            @keyframes pulseGlow {
                0% { transform: scale(0.9); opacity: 0.5; box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }
                70% { transform: scale(1); opacity: 1; box-shadow: 0 0 0 10px rgba(16, 185, 129, 0); }
                100% { transform: scale(0.9); opacity: 0.5; box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
            }

            .main-container {
                display: grid;
                grid-template-columns: 1fr;
                gap: 2.5rem;
                width: 100%;
                max-width: 1200px;
                z-index: 10;
                animation: fadeInUp 1s ease-out 0.2s both;
            }

            @keyframes fadeInUp {
                from { opacity: 0; transform: translateY(30px); }
                to { opacity: 1; transform: translateY(0); }
            }

            @media(min-width: 900px) {
                .main-container {
                    grid-template-columns: 1fr 1.2fr;
                }
            }

            /* Futuristic Glass Card */
            .card {
                background: var(--card-glass);
                border: 1px solid var(--card-border);
                border-radius: 24px;
                padding: 2.5rem;
                backdrop-filter: blur(20px);
                -webkit-backdrop-filter: blur(20px);
                box-shadow: var(--panel-shadow);
                position: relative;
                overflow: hidden;
                transition: all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);
            }

            .card::after {
                content: '';
                position: absolute;
                top: 0; left: 0; width: 100%; height: 2px;
                background: linear-gradient(90deg, transparent, var(--secondary), transparent);
                opacity: 0;
                transition: opacity 0.4s;
            }

            .card:hover {
                border-color: rgba(0, 223, 216, 0.3);
                transform: translateY(-5px);
                box-shadow: 0 30px 60px rgba(0, 0, 0, 0.7);
            }

            .card:hover::after {
                opacity: 1;
            }

            .card-title {
                font-family: 'Outfit', sans-serif;
                font-size: 1.5rem;
                font-weight: 700;
                margin-bottom: 2rem;
                display: flex;
                align-items: center;
                gap: 1rem;
                color: #fff;
            }

            .card-title svg {
                color: var(--secondary);
                filter: drop-shadow(0 0 8px var(--secondary));
            }

            /* Form Elements */
            .form-group {
                margin-bottom: 1.8rem;
            }

            .form-group label {
                display: block;
                font-size: 0.75rem;
                font-weight: 700;
                color: var(--text-dim);
                margin-bottom: 0.7rem;
                text-transform: uppercase;
                letter-spacing: 0.15em;
            }

            .input-wrapper {
                position: relative;
            }

            .form-group input {
                width: 100%;
                background: rgba(0, 0, 0, 0.4);
                border: 1px solid var(--card-border);
                border-radius: 14px;
                padding: 1.1rem 1.2rem;
                font-family: 'Inter', sans-serif;
                font-size: 1rem;
                color: #fff;
                transition: all 0.3s ease;
                outline: none;
                cursor: text;
            }

            .form-group input:focus {
                border-color: var(--secondary);
                background: rgba(0, 223, 216, 0.03);
                box-shadow: 0 0 20px rgba(0, 223, 216, 0.1);
            }

            .form-group input::placeholder {
                color: #475569;
            }

            .btn-container {
                display: flex;
                gap: 1.2rem;
                margin-top: 2.5rem;
            }

            button {
                flex: 1;
                font-family: 'Outfit', sans-serif;
                font-weight: 700;
                font-size: 1rem;
                padding: 1.1rem 1.5rem;
                border: none;
                border-radius: 14px;
                cursor: pointer;
                transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 0.7rem;
                position: relative;
                overflow: hidden;
                z-index: 1;
            }

            button::before {
                content: '';
                position: absolute;
                top: 0; left: -100%; width: 100%; height: 100%;
                background: linear-gradient(90deg, transparent, rgba(255,255,255,0.2), transparent);
                transition: left 0.5s;
                z-index: -1;
            }

            button:hover::before {
                left: 100%;
            }

            button:active {
                transform: scale(0.96);
            }

            .btn-primary {
                background: linear-gradient(135deg, var(--primary) 0%, #4c1d95 100%);
                color: #fff;
                box-shadow: 0 10px 25px rgba(121, 40, 202, 0.3);
            }

            .btn-primary:hover {
                box-shadow: 0 15px 35px rgba(121, 40, 202, 0.5);
                filter: brightness(1.1);
            }

            .btn-secondary {
                background: rgba(255, 255, 255, 0.03);
                color: var(--text-main);
                border: 1px solid var(--card-border);
                backdrop-filter: blur(10px);
            }

            .btn-secondary:hover {
                background: rgba(255, 255, 255, 0.08);
                border-color: var(--text-dim);
            }

            /* Terminal Aesthetic */
            .console-card {
                display: flex;
                flex-direction: column;
                height: 100%;
                border: 1px solid rgba(0, 223, 216, 0.15);
            }

            .console-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 1.5rem;
                padding-bottom: 1rem;
                border-bottom: 1px solid rgba(255,255,255,0.05);
            }

            .console-area {
                flex-grow: 1;
                background: #020408;
                border-radius: 16px;
                padding: 1.5rem;
                font-family: 'Fira Code', monospace;
                font-size: 0.85rem;
                line-height: 1.6;
                color: #a5f3fc;
                overflow-y: auto;
                max-height: 480px;
                white-space: pre-wrap;
                box-shadow: inset 0 4px 20px rgba(0,0,0,0.8);
                border: 1px solid rgba(255,255,255,0.03);
            }

            .console-area::-webkit-scrollbar {
                width: 5px;
            }
            .console-area::-webkit-scrollbar-thumb {
                background: rgba(0, 223, 216, 0.2);
                border-radius: 10px;
            }

            /* Custom Toasts */
            .toast {
                position: fixed;
                bottom: 2.5rem;
                right: 2.5rem;
                background: rgba(13, 17, 28, 0.95);
                backdrop-filter: blur(20px);
                border: 1px solid var(--card-border);
                padding: 1.2rem 2rem;
                border-radius: 16px;
                box-shadow: 0 20px 40px rgba(0,0,0,0.4);
                display: flex;
                align-items: center;
                gap: 1rem;
                transform: translateX(200%);
                transition: transform 0.5s cubic-bezier(0.68, -0.55, 0.265, 1.55);
                z-index: 1000;
            }

            .toast.show { transform: translateX(0); }
            .toast-success { border-left: 4px solid var(--success); }
            .toast-error { border-left: 4px solid var(--error); }

            .loader-dots {
                display: inline-flex;
                gap: 4px;
            }
            .loader-dots span {
                width: 4px; height: 4px;
                background: currentColor;
                border-radius: 50%;
                animation: dotBlink 1.4s infinite;
            }
            .loader-dots span:nth-child(2) { animation-delay: 0.2s; }
            .loader-dots span:nth-child(3) { animation-delay: 0.4s; }

            @keyframes dotBlink {
                0%, 80%, 100% { opacity: 0; }
                40% { opacity: 1; }
            }

            .spinner {
                width: 20px; height: 20px;
                border: 2px solid rgba(255,255,255,0.2);
                border-top-color: #fff;
                border-radius: 50%;
                animation: spin 0.8s linear infinite;
                display: none;
            }

            @keyframes spin { to { transform: rotate(360deg); } }

            footer {
                margin-top: 5rem;
                color: var(--text-dim);
                font-size: 0.85rem;
                text-align: center;
                letter-spacing: 0.05em;
            }

            /* Safety Toggle Styling */
            .toggle-container {
                display: flex;
                align-items: center;
                gap: 1rem;
                margin-bottom: 2rem;
                padding: 1rem;
                background: rgba(255, 255, 255, 0.03);
                border-radius: 12px;
                border: 1px solid rgba(255, 255, 255, 0.05);
            }

            .toggle-label {
                font-size: 0.85rem;
                font-weight: 600;
                color: var(--text-dim);
                flex-grow: 1;
            }

            .switch {
                position: relative;
                display: inline-block;
                width: 44px;
                height: 24px;
            }

            .switch input {
                opacity: 0;
                width: 0;
                height: 0;
            }

            .slider {
                position: absolute;
                cursor: pointer;
                top: 0; left: 0; right: 0; bottom: 0;
                background-color: rgba(255, 255, 255, 0.1);
                transition: .4s;
                border-radius: 34px;
                border: 1px solid rgba(255, 255, 255, 0.1);
            }

            .slider:before {
                position: absolute;
                content: "";
                height: 16px;
                width: 16px;
                left: 3px;
                bottom: 3px;
                background-color: var(--text-dim);
                transition: .4s;
                border-radius: 50%;
            }

            input:checked + .slider {
                background-color: rgba(121, 40, 202, 0.2);
                border-color: var(--primary);
            }

            input:checked + .slider:before {
                transform: translateX(20px);
                background-color: var(--secondary);
                box-shadow: 0 0 10px var(--secondary);
            }

            input:readonly {
                opacity: 0.6;
                background: rgba(255, 255, 255, 0.02) !important;
                border-style: dashed !important;
            }

            /* PIN Gate Styles */
            #pin-gate {
                position: fixed;
                top: 0; left: 0; width: 100%; height: 100%;
                background: var(--bg-deep);
                display: flex;
                flex-direction: column;
                justify-content: center;
                align-items: center;
                z-index: 9999;
                backdrop-filter: blur(40px);
            }

            .pin-card {
                background: var(--card-glass);
                padding: 3rem;
                border-radius: 30px;
                border: 1px solid var(--border-glow);
                text-align: center;
                max-width: 400px;
                width: 90%;
                box-shadow: 0 0 50px rgba(121, 40, 202, 0.2);
            }

            .pin-card h2 {
                font-family: 'Outfit', sans-serif;
                margin-bottom: 1rem;
                font-size: 1.8rem;
                background: linear-gradient(to right, #fff, var(--secondary));
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }

            .pin-card p {
                color: var(--text-dim);
                font-size: 0.9rem;
                margin-bottom: 2rem;
            }

            .pin-input-group {
                display: flex;
                gap: 10px;
                justify-content: center;
                margin-bottom: 2rem;
            }

            .pin-input {
                width: 50px;
                height: 60px;
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 12px;
                font-size: 2rem;
                text-align: center;
                color: var(--secondary);
                outline: none;
                transition: all 0.3s;
            }

            .pin-input:focus {
                border-color: var(--secondary);
                box-shadow: 0 0 15px rgba(0, 223, 216, 0.2);
                background: rgba(0, 223, 216, 0.05);
            }

            #main-content {
                display: none;
                width: 100%;
                max-width: 1200px;
            }

            /* --- NEW P&L / LEDGER JOURNAL STYLING --- */
            .journal-container {
                display: flex;
                flex-direction: column;
                gap: 1.5rem;
            }

            .table-container {
                background: var(--card-glass);
                border: 1px solid var(--card-border);
                border-radius: 24px;
                padding: 2.5rem;
                backdrop-filter: blur(20px);
                box-shadow: var(--panel-shadow);
                overflow-x: auto;
            }

            .table-header-row {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 2rem;
                flex-wrap: wrap;
                gap: 1rem;
            }

            .view-selector {
                display: flex;
                background: rgba(0, 0, 0, 0.4);
                padding: 0.3rem;
                border-radius: 12px;
                border: 1px solid rgba(255, 255, 255, 0.05);
            }

            .view-btn {
                background: transparent;
                border: none;
                padding: 0.5rem 1.2rem;
                border-radius: 9px;
                font-size: 0.8rem;
                font-weight: 600;
                color: var(--text-dim);
                cursor: pointer;
                transition: all 0.3s;
                flex: none;
            }

            .view-btn.active {
                background: var(--primary);
                color: #fff;
                box-shadow: 0 4px 12px rgba(121, 40, 202, 0.4);
            }

            .stats-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
                gap: 1.5rem;
            }

            .stat-card {
                background: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 20px;
                padding: 1.5rem;
                backdrop-filter: blur(20px);
                display: flex;
                flex-direction: column;
                gap: 0.5rem;
                position: relative;
                overflow: hidden;
                box-shadow: inset 0 2px 10px rgba(255, 255, 255, 0.01);
                transition: all 0.3s ease;
            }

            .stat-card::before {
                content: '';
                position: absolute;
                top: 0; left: 0; width: 4px; height: 100%;
                background: var(--text-dim);
            }

            .stat-card.stat-profit::before { background: var(--success); }
            .stat-card.stat-loss::before { background: var(--error); }
            .stat-card.stat-neutral::before { background: var(--secondary); }

            .stat-card.stat-profit {
                box-shadow: 0 0 20px rgba(16, 185, 129, 0.05);
                border-color: rgba(16, 185, 129, 0.15);
            }

            .stat-card.stat-loss {
                box-shadow: 0 0 20px rgba(239, 68, 68, 0.05);
                border-color: rgba(239, 68, 68, 0.15);
            }

            .stat-card.stat-active {
                box-shadow: 0 0 20px rgba(0, 223, 216, 0.08);
                border-color: rgba(0, 223, 216, 0.2);
            }

            .stat-label {
                font-size: 0.75rem;
                font-weight: 700;
                color: var(--text-dim);
                text-transform: uppercase;
                letter-spacing: 0.1em;
            }

            .stat-value {
                font-family: 'Outfit', sans-serif;
                font-size: 1.8rem;
                font-weight: 800;
                color: #fff;
            }

            .stat-value.profit {
                color: var(--success);
                text-shadow: 0 0 12px rgba(16, 185, 129, 0.3);
            }

            .stat-value.loss {
                color: var(--error);
                text-shadow: 0 0 12px rgba(239, 68, 68, 0.3);
            }

            .journal-table {
                width: 100%;
                border-collapse: collapse;
                text-align: left;
                font-size: 0.9rem;
            }

            .journal-table th {
                padding: 1rem;
                color: var(--text-dim);
                font-weight: 600;
                border-bottom: 1px solid rgba(255, 255, 255, 0.05);
                text-transform: uppercase;
                font-size: 0.75rem;
                letter-spacing: 0.05em;
            }

            .journal-table td {
                padding: 1.2rem 1rem;
                border-bottom: 1px solid rgba(255, 255, 255, 0.02);
                color: #fff;
                vertical-align: middle;
            }

            .journal-table tr:hover td {
                background: rgba(255, 255, 255, 0.02);
            }

            .badge {
                display: inline-flex;
                align-items: center;
                padding: 0.25rem 0.75rem;
                border-radius: 100px;
                font-size: 0.75rem;
                font-weight: 700;
                letter-spacing: 0.03em;
            }

            .badge-buy { background: rgba(16, 185, 129, 0.1); color: var(--success); border: 1px solid rgba(16, 185, 129, 0.2); }
            .badge-sell { background: rgba(239, 68, 68, 0.1); color: var(--error); border: 1px solid rgba(239, 68, 68, 0.2); }
            .badge-open { background: rgba(0, 223, 216, 0.1); color: var(--secondary); border: 1px solid rgba(0, 223, 216, 0.2); box-shadow: 0 0 10px rgba(0, 223, 216, 0.1); }
            .badge-closed { background: rgba(255, 255, 255, 0.05); color: var(--text-dim); border: 1px solid rgba(255, 255, 255, 0.1); }
            .badge-rejected { background: rgba(239, 68, 68, 0.05); color: #64748b; border: 1px solid rgba(255, 255, 255, 0.05); cursor: help; }

            .profit { color: var(--success) !important; }
            .loss { color: var(--error) !important; }
        </style>
    </head>
    <body>
        <div id="pin-gate">
            <div class="pin-card">
                <h2>SECURITY CLEARANCE</h2>
                <p>Please enter your 4-digit Admin PIN to access the Regal Algo Command Center.</p>
                <div class="pin-input-group">
                    <input type="password" maxlength="1" class="pin-input" onkeyup="moveFocus(this, 1)">
                    <input type="password" maxlength="1" class="pin-input" onkeyup="moveFocus(this, 2)">
                    <input type="password" maxlength="1" class="pin-input" onkeyup="moveFocus(this, 3)">
                    <input type="password" maxlength="1" class="pin-input" onkeyup="moveFocus(this, 4)">
                </div>
                <div id="pin-error" style="color: var(--error); font-size: 0.8rem; margin-top: -1rem; display: none;">Invalid Access Code. Access Denied.</div>
            </div>
        </div>

        <div id="main-content">
            <header>
                <div class="logo-container">
                    <h1>REGAL ALGO</h1>
                    <p>powered by Duocore Softwares</p>
                </div>
                <div class="status-container">
                    <div class="status-badge" id="bot-status">CORE SYSTEM ACTIVE</div>
                </div>
            </header>

            <div class="main-container">
            <!-- Configuration Settings -->
            <div class="card">
                <div class="card-title">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>
                    Command Center
                </div>

                <div class="toggle-container">
                    <span class="toggle-label">Vault Protection (Client ID & Secret)</span>
                    <label class="switch">
                        <input type="checkbox" id="safety-toggle" checked onchange="toggleProtection()">
                        <span class="slider"></span>
                    </label>
                </div>
                
                <form id="config-form" onsubmit="saveConfig(event)">
                    <div class="form-group">
                        <label for="client-id">Dhan Client Identity</label>
                        <input type="text" id="client-id" required placeholder="Enter Client ID">
                    </div>
                    
                    <div class="form-group">
                        <label for="access-token">Dhan API Vault Key</label>
                        <input type="password" id="access-token" required placeholder="Enter Access Token">
                    </div>

                    <div class="form-group">
                        <label for="secret-token">Signal Secret Token</label>
                        <input type="text" id="secret-token" required placeholder="Enter Secret Token">
                    </div>

                    <div class="btn-container">
                        <button type="button" class="btn-secondary" onclick="testConnection()">
                            <div class="spinner" id="test-spinner"></div>
                            <span id="test-btn-text">Check Connection</span>
                        </button>
                        <button type="submit" class="btn-primary">
                            <div class="spinner" id="save-spinner"></div>
                            <span id="save-btn-text">Initialize Sync</span>
                        </button>
                    </div>
                </form>
            </div>

            <!-- Live Monitoring Terminal -->
            <div class="card console-card">
                <div class="console-header">
                    <div class="card-title" style="margin-bottom: 0;">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
                        Neural Log Stream
                    </div>
                    <div style="display: flex; gap: 6px;">
                        <span style="width: 8px; height: 8px; border-radius: 50%; background: #ef4444; opacity: 0.6;"></span>
                        <span style="width: 8px; height: 8px; border-radius: 50%; background: #f59e0b; opacity: 0.6;"></span>
                        <span style="width: 8px; height: 8px; border-radius: 50%; background: #10b981; opacity: 0.6;"></span>
                    </div>
                </div>
                <div class="console-area" id="terminal-logs">Establishing link to neural stream...</div>
            </div>
            </div> <!-- End of main-container -->

            <!-- P&L and Trade Journal Section -->
            <div class="journal-container" style="width: 100%; max-width: 1200px; margin-top: 2.5rem; z-index: 10; animation: fadeInUp 1s ease-out 0.4s both;">
                <div class="table-container">
                    <div class="table-header-row">
                        <div class="card-title" style="margin-bottom: 0;">
                            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"></line><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"></path></svg>
                            Quant Ledger & P&L Stream
                        </div>
                        <div class="view-selector">
                            <button type="button" class="view-btn" data-view="real" onclick="setView('real')">Real Money</button>
                            <button type="button" class="view-btn active" data-view="simulated" onclick="setView('simulated')">Simulated (Paper)</button>
                        </div>
                    </div>

                    <!-- Statistics Cards Grid inside the container -->
                    <div class="stats-grid" style="margin-bottom: 2rem;">
                        <div class="stat-card" id="card-today-pl">
                            <span class="stat-label">Today's P&L</span>
                            <span class="stat-value" id="stat-today-pl">₹0.00</span>
                        </div>
                        <div class="stat-card" id="card-total-pl">
                            <span class="stat-label">Net Realized P&L</span>
                            <span class="stat-value" id="stat-total-pl">₹0.00</span>
                        </div>
                        <div class="stat-card stat-neutral" id="card-win-rate">
                            <span class="stat-label">Quant Win Rate</span>
                            <span class="stat-value" id="stat-win-rate">0.0%</span>
                            <span style="font-size: 0.75rem; color: var(--text-dim); margin-top: 2px;" id="stat-trades-count">0 Closed</span>
                        </div>
                        <div class="stat-card stat-neutral" id="card-active-position">
                            <span class="stat-label">Active Position</span>
                            <span class="stat-value" id="stat-active-position" style="font-size: 1.25rem; font-family: sans-serif; font-weight: 600; letter-spacing: normal; margin-top: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">NONE</span>
                        </div>
                    </div>

                    <!-- Historical Trade Journal Table -->
                    <div style="overflow-x: auto; width: 100%;">
                        <table class="journal-table">
                            <thead>
                                <tr>
                                    <th>Entry Time</th>
                                    <th>Contract Symbol</th>
                                    <th>Side / Qty</th>
                                    <th>Avg Entry</th>
                                    <th>Avg Exit</th>
                                    <th>P&L (₹)</th>
                                    <th>Status</th>
                                </tr>
                            </thead>
                            <tbody id="journal-tbody">
                                <tr>
                                    <td colspan="7" style="text-align: center; color: var(--text-dim); padding: 3rem;">
                                        Loading ledger data...
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <footer>
                &copy; 2026 Duocore Softwares | Regal Algo Engine v4.6
            </footer>
        </div>

        <div class="toast" id="toast-notif">
            <span id="toast-text"></span>
        </div>

        <script>
            let adminPin = "";
            let activeView = "simulated"; // 'real' or 'simulated'
            let tradesData = null;

            function moveFocus(el, index) {
                if (el.value.length === 1 && index < 4) {
                    el.nextElementSibling.focus();
                }
                
                // Collect full PIN
                const inputs = document.querySelectorAll('.pin-input');
                let currentPin = "";
                inputs.forEach(input => currentPin += input.value);
                
                if (currentPin.length === 4) {
                    verifyPin(currentPin);
                }
            }

            function verifyPin(pin) {
                if (pin === "1502") {
                    adminPin = pin;
                    document.getElementById('pin-gate').style.display = 'none';
                    document.getElementById('main-content').style.display = 'block';
                    fetchConfig();
                    setInterval(fetchLogs, 2000);
                    setInterval(fetchTrades, 3000);
                    fetchTrades();
                } else {
                    document.getElementById('pin-error').style.display = 'block';
                    // Clear inputs
                    document.querySelectorAll('.pin-input').forEach(input => input.value = "");
                    document.querySelectorAll('.pin-input')[0].focus();
                }
            }

            function showToast(text, type = 'success') {
                const toast = document.getElementById('toast-notif');
                const toastText = document.getElementById('toast-text');
                toast.className = 'toast ' + (type === 'success' ? 'toast-success' : 'toast-error');
                toastText.innerText = text;
                toast.classList.add('show');
                setTimeout(() => toast.classList.remove('show'), 4000);
            }

            async function fetchConfig() {
                try {
                    const res = await fetch('/api/config');
                    const data = await res.json();
                    document.getElementById('client-id').value = data.CLIENT_ID || '';
                    document.getElementById('access-token').value = data.ACCESS_TOKEN || '';
                    document.getElementById('secret-token').value = data.SECRET_TOKEN || '';
                    toggleProtection(); // Apply protection based on toggle default
                } catch (e) {
                    showToast('Failed to sync vault.', 'error');
                }
            }

            function toggleProtection() {
                const isLocked = document.getElementById('safety-toggle').checked;
                const clientId = document.getElementById('client-id');
                const secretToken = document.getElementById('secret-token');
                
                clientId.readOnly = isLocked;
                secretToken.readOnly = isLocked;
                
                if(isLocked) {
                    clientId.title = "Vault Locked - Disable protection to edit";
                    secretToken.title = "Vault Locked - Disable protection to edit";
                } else {
                    clientId.title = "";
                    secretToken.title = "";
                }
            }

            async function saveConfig(e) {
                e.preventDefault();
                const saveSpinner = document.getElementById('save-spinner');
                const saveBtnText = document.getElementById('save-btn-text');
                const logArea = document.getElementById('terminal-logs');
                
                saveSpinner.style.display = 'block';
                saveBtnText.innerText = 'Syncing...';

                const now = new Date().toLocaleTimeString();
                logArea.innerHTML = `<span style="color:var(--secondary)">[${now}]</span> 🚀 <span style="color:#fff; font-weight:bold;">INITIALIZING QUANT DEPLOYMENT...</span>\n`;
                logArea.innerHTML += `<span style="color:#64748b">---------------------------------------------------</span>\n`;
                logArea.scrollTop = logArea.scrollHeight;

                const payload = {
                    CLIENT_ID: document.getElementById('client-id').value.trim(),
                    ACCESS_TOKEN: document.getElementById('access-token').value.trim(),
                    SECRET_TOKEN: document.getElementById('secret-token').value.trim(),
                    pin: adminPin
                };

                try {
                    const res = await fetch('/api/config', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    const result = await res.json();
                    
                    if(result.pipeline) {
                        let text = logArea.innerHTML;
                        result.pipeline.forEach((step, i) => {
                            const icon = step.status === 'success' ? '✅' : '❌';
                            const color = step.status === 'success' ? 'var(--success)' : 'var(--error)';
                            text += `<span style="color:${color}">${icon} Step ${i+1}: ${step.step}</span>\n`;
                            text += `   <span style="color:#64748b">↳ ${step.detail}</span>\n\n`;
                        });
                        const endTime = new Date().toLocaleTimeString();
                        text += `<span style="color:#64748b">---------------------------------------------------</span>\n`;
                        text += `<span style="color:var(--secondary)">[${endTime}]</span> 🎉 <span style="color:#fff;">QUANT SYSTEMS SECURED & DEPLOYED.</span>`;
                        logArea.innerHTML = text;
                        logArea.scrollTop = logArea.scrollHeight;
                        showToast('Deployment Successful', 'success');
                    }
                } catch(err) {
                    showToast('Deployment Failed', 'error');
                } finally {
                    saveSpinner.style.display = 'none';
                    saveBtnText.innerText = 'Initialize Sync';
                }
            }

            async function testConnection() {
                const testSpinner = document.getElementById('test-spinner');
                const testBtnText = document.getElementById('test-btn-text');
                testSpinner.style.display = 'block';
                testBtnText.innerText = 'Checking...';
                try {
                    const res = await fetch('/api/test', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            CLIENT_ID: document.getElementById('client-id').value.trim(),
                            ACCESS_TOKEN: document.getElementById('access-token').value.trim()
                        })
                    });
                    const result = await res.json();
                    if(result.status === 'success') {
                        showToast('Auth verified. Balance: ₹' + result.balance, 'success');
                    } else {
                        showToast('Auth denied: ' + result.error, 'error');
                    }
                } catch(err) {
                    showToast('Link error.', 'error');
                } finally {
                    testSpinner.style.display = 'none';
                    testBtnText.innerText = 'Check Connection';
                }
            }

            async function fetchLogs() {
                try {
                    const res = await fetch('/api/logs');
                    const data = await res.json();
                    const logArea = document.getElementById('terminal-logs');
                    const isScrolledToBottom = logArea.scrollHeight - logArea.clientHeight <= logArea.scrollTop + 50;
                    if(data.logs) {
                        // Minimal formatting for logs
                        logArea.innerText = data.logs;
                    }
                    if (isScrolledToBottom) logArea.scrollTop = logArea.scrollHeight;
                } catch(e) {}
            }

            async function fetchTrades() {
                try {
                    const res = await fetch('/api/trades');
                    const data = await res.json();
                    if(data.status === 'success') {
                        tradesData = data;
                        renderTrades();
                    }
                } catch(e) {
                    console.error("Error fetching trades:", e);
                }
            }

            function setView(view) {
                activeView = view;
                document.querySelectorAll('.view-btn').forEach(btn => {
                    if (btn.getAttribute('data-view') === view) {
                        btn.classList.add('active');
                    } else {
                        btn.classList.remove('active');
                    }
                });
                renderTrades();
            }

            function renderTrades() {
                if(!tradesData) return;
                
                const s = tradesData.summary;
                const isReal = activeView === 'real';
                
                // Update stats
                const todayPlEl = document.getElementById('stat-today-pl');
                const totalPlEl = document.getElementById('stat-total-pl');
                const winRateEl = document.getElementById('stat-win-rate');
                const countEl = document.getElementById('stat-trades-count');
                
                const todayPl = isReal ? s.today_real_p_l : s.today_sim_p_l;
                const totalPl = isReal ? s.real_p_l : s.sim_p_l;
                const winRate = isReal ? s.real_win_rate : s.sim_win_rate;
                const count = isReal ? s.real_closed_count : s.sim_closed_count;
                
                // Formatted outputs
                todayPlEl.innerText = (todayPl >= 0 ? "₹" : "-₹") + Math.abs(todayPl).toFixed(2);
                todayPlEl.className = "stat-value " + (todayPl > 0 ? "profit" : (todayPl < 0 ? "loss" : ""));
                
                totalPlEl.innerText = (totalPl >= 0 ? "₹" : "-₹") + Math.abs(totalPl).toFixed(2);
                totalPlEl.className = "stat-value " + (totalPl > 0 ? "profit" : (totalPl < 0 ? "loss" : ""));
                
                // Color card borders/indicators
                document.getElementById('card-today-pl').className = "stat-card " + (todayPl > 0 ? "stat-profit" : (todayPl < 0 ? "stat-loss" : "stat-neutral"));
                document.getElementById('card-total-pl').className = "stat-card " + (totalPl > 0 ? "stat-profit" : (totalPl < 0 ? "stat-loss" : "stat-neutral"));
                
                winRateEl.innerText = winRate.toFixed(1) + "%";
                countEl.innerText = count + " Closed";
                
                // Active Position Check
                const activePosEl = document.getElementById('stat-active-position');
                const activeCard = document.getElementById('card-active-position');
                const openTrades = tradesData.trades.filter(t => t.status.includes("OPEN"));
                const currentOpen = openTrades.find(t => {
                    const isSimTrade = t.status.includes("Simulated") || t.buy_order_id === 'SIMULATED';
                    return isReal ? !isSimTrade : isSimTrade;
                });
                
                if (currentOpen) {
                    activePosEl.innerText = `${currentOpen.option_type} (${parseFloat(currentOpen.strike)}) @ ₹${parseFloat(currentOpen.buy_price).toFixed(2)}`;
                    activePosEl.title = `Symbol: ${currentOpen.symbol} | Strike: ${currentOpen.strike} | Qty: ${currentOpen.quantity}`;
                    activeCard.className = "stat-card stat-active";
                } else {
                    activePosEl.innerText = "NONE";
                    activePosEl.title = "";
                    activeCard.className = "stat-card stat-neutral";
                }
                
                // Render Table
                const tbody = document.getElementById('journal-tbody');
                tbody.innerHTML = "";
                
                const filteredTrades = tradesData.trades.filter(t => {
                    const isSimTrade = t.status.includes("Simulated") || t.buy_order_id === 'SIMULATED' || t.sell_order_id === 'SIMULATED';
                    return isReal ? !isSimTrade : isSimTrade;
                });
                
                if (filteredTrades.length === 0) {
                    tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-dim); padding: 3rem;">No trades recorded in this category. Signals will populate this area.</td></tr>`;
                    return;
                }
                
                filteredTrades.forEach(t => {
                    const pl = parseFloat(t.p_l || 0);
                    const isClosed = t.status.includes("CLOSED");
                    const isOpen = t.status.includes("OPEN");
                    const isRejected = t.status.includes("REJECTED") || t.status.includes("failed");
                    
                    let statusBadge = "";
                    if (isOpen) statusBadge = `<span class="badge badge-open">OPEN</span>`;
                    else if (isClosed) statusBadge = `<span class="badge badge-closed">CLOSED</span>`;
                    else if (isRejected) statusBadge = `<span class="badge badge-rejected" title="${t.remarks || 'Order Rejected'}">REJECTED</span>`;
                    else statusBadge = `<span class="badge badge-closed">${t.status}</span>`;
                    
                    let plText = "—";
                    let plClass = "";
                    if (isClosed) {
                        plText = (pl >= 0 ? "+₹" : "-₹") + Math.abs(pl).toFixed(2);
                        plClass = pl >= 0 ? "profit" : "loss";
                    } else if (isOpen) {
                        plText = "OPEN";
                        plClass = "profit"; 
                    }
                    
                    const buyTimeShort = t.buy_time ? t.buy_time.split(" ")[1] : "—";
                    const buyDateShort = t.buy_time ? t.buy_time.split(" ")[0].substring(5) : "—"; // MM-DD
                    
                    const exitTimeStr = t.sell_time ? t.sell_time.split(" ")[1] : "—";
                    
                    tbody.innerHTML += `
                        <tr>
                            <td>
                                <div style="font-weight: 600;">${buyTimeShort}</div>
                                <div style="font-size: 0.75rem; color: var(--text-dim);">${buyDateShort}</div>
                            </td>
                            <td>
                                <div style="font-weight: 700; color: #fff;">${t.symbol} ${parseFloat(t.strike || 0)} ${t.option_type}</div>
                                <div style="font-size: 0.75rem; color: var(--text-dim);">${t.buy_order_id === 'SIMULATED' ? 'Paper Trade' : t.buy_order_id}</div>
                            </td>
                            <td>
                                <span class="badge badge-buy">BUY</span>
                                <span style="font-weight: 600; margin-left: 0.5rem;">${t.quantity} Qty</span>
                            </td>
                            <td style="font-family: 'Fira Code', monospace; font-weight: 500;">₹${parseFloat(t.buy_price || 0).toFixed(2)}</td>
                            <td style="font-family: 'Fira Code', monospace; font-weight: 500;">
                                ${t.sell_price ? '₹' + parseFloat(t.sell_price).toFixed(2) : '—'}
                            </td>
                            <td style="font-family: 'Fira Code', monospace; font-weight: 700;" class="${plClass}">
                                ${plText}
                            </td>
                            <td>${statusBadge}</td>
                        </tr>
                    `;
                });
            }

            window.onload = () => {
                const firstInput = document.querySelectorAll('.pin-input')[0];
                if (firstInput) firstInput.focus();
            };
        </script>
    </body>
    </html>
    """
    return render_template_string(html_template)

@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    if request.method == 'GET':
        config = load_config()
        # Clean token for safety representation (do not mask completely since they might want to see if it is there)
        return jsonify({
            "CLIENT_ID": config.get("CLIENT_ID", ""),
            "ACCESS_TOKEN": config.get("ACCESS_TOKEN", ""),
            "SECRET_TOKEN": config.get("SECRET_TOKEN", "")
        })
    
    elif request.method == 'POST':
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"status": "failed", "remarks": "Invalid body"}), 400
        
        # PIN VERIFICATION
        if data.get('pin') != "1502":
            return jsonify({"status": "failed", "remarks": "Security Breach: Invalid Admin PIN"}), 403
        
        pipeline_steps = save_and_deploy(data)
        all_ok = all(s['status'] in ('success', 'warning', 'skipped') for s in pipeline_steps)
        return jsonify({"status": "success" if all_ok else "partial", "pipeline": pipeline_steps}), 200

@app.route('/api/test', methods=['POST'])
def api_test_connection():
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"status": "failed", "error": "Missing params"}), 400
    
    client_id = data.get('CLIENT_ID')
    access_token = data.get('ACCESS_TOKEN')
    
    try:
        # Test Dhan API Connection
        test_context = DhanContext(client_id=client_id, access_token=access_token)
        test_dhan = dhanhq(test_context)
        res = test_dhan.get_fund_limits()
        
        if isinstance(res, dict) and res.get('status') == 'success':
            # Extract available margin for proof of work
            avail_balance = res.get('data', {}).get('availabelBalance', 0.0)
            return jsonify({"status": "success", "balance": avail_balance}), 200
        else:
            err_msg = res.get('remarks', {}).get('error_message') or "Client ID or Access Token invalid."
            return jsonify({"status": "failed", "error": err_msg}), 200
    except Exception as e:
        return jsonify({"status": "failed", "error": str(e)}), 200

@app.route('/api/trades', methods=['GET'])
def api_get_trades():
    try:
        trades = get_all_trades()
        # Sort trades by newest first (reverse the list)
        trades = trades[::-1]
        
        # Calculate summary statistics
        real_p_l = 0.0
        sim_p_l = 0.0
        real_wins = 0
        real_closed = 0
        sim_wins = 0
        sim_closed = 0
        
        for t in trades:
            status = t.get('status', '')
            p_l_val = float(t.get('p_l', 0.0) or 0.0)
            
            if "Simulated" in status or t.get('buy_order_id') == 'SIMULATED' or t.get('sell_order_id') == 'SIMULATED':
                if "CLOSED" in status:
                    sim_p_l += p_l_val
                    sim_closed += 1
                    if p_l_val > 0:
                        sim_wins += 1
            else:
                if "CLOSED" in status:
                    real_p_l += p_l_val
                    real_closed += 1
                    if p_l_val > 0:
                        real_wins += 1
                        
        # Today's P&L calculation
        today_str = get_ist_now().strftime("%Y-%m-%d")
        today_real_p_l = 0.0
        today_sim_p_l = 0.0
        
        for t in trades:
            status = t.get('status', '')
            p_l_val = float(t.get('p_l', 0.0) or 0.0)
            sell_time = t.get('sell_time', '')
            
            if sell_time.startswith(today_str):
                if "Simulated" in status or t.get('buy_order_id') == 'SIMULATED' or t.get('sell_order_id') == 'SIMULATED':
                    today_sim_p_l += p_l_val
                else:
                    today_real_p_l += p_l_val
                    
        return jsonify({
            "status": "success",
            "trades": trades,
            "summary": {
                "real_p_l": round(real_p_l, 2),
                "sim_p_l": round(sim_p_l, 2),
                "today_real_p_l": round(today_real_p_l, 2),
                "today_sim_p_l": round(today_sim_p_l, 2),
                "real_win_rate": round((real_wins / real_closed * 100), 1) if real_closed > 0 else 0,
                "sim_win_rate": round((sim_wins / sim_closed * 100), 1) if sim_closed > 0 else 0,
                "real_closed_count": real_closed,
                "sim_closed_count": sim_closed
            }
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/logs', methods=['GET'])
def api_get_logs():
    try:
        log_file = "bot_logs.txt"
        if os.path.exists(log_file):
            with open(log_file, "r") as f:
                # Read last 60 lines for a cleaner viewport
                lines = f.readlines()
                last_lines = lines[-60:] if len(lines) > 60 else lines
                return jsonify({"logs": "".join(last_lines)}), 200
        return jsonify({"logs": "Log file not found yet. Generate logs by firing webhook alerts!"}), 200
    except Exception as e:
        return jsonify({"logs": f"Error reading logs: {e}"}), 500

@app.route('/api/git-pull-and-reload', methods=['POST'])
def api_pull_and_reload():
    data = request.get_json(force=True, silent=True) or {}
    config = load_config()
    
    # Authorize with Secret Token
    if data.get('secret') != config.get('SECRET_TOKEN', 'JunnarTrader2026'):
        print("🔴 Unauthorized remote sync attempt!")
        return jsonify({"status": "error", "remarks": "Unauthorized"}), 403
    
    print("📥 Received remote sync request from Local Dashboard...")
    
    # Run git pull
    success = run_and_log_command(["git", "pull", "origin", "main"])
    if success:
        print("✅ Git Pull completed on EC2! Reloading new credentials...")
        return jsonify({"status": "success", "message": "Git pull and configuration reload completed on EC2!"}), 200
    else:
        print("❌ Git pull failed on EC2.")
        return jsonify({"status": "failed", "message": "Git pull failed on EC2."}), 500

if __name__ == '__main__':
    # Initialize global default client fallback from config
    global_config = load_config()
    dhan_context = DhanContext(client_id=global_config.get('CLIENT_ID'), access_token=global_config.get('ACCESS_TOKEN'))
    dhan = dhanhq(dhan_context)
    
    # Listen on all public IPs on port 80
    app.run(host='0.0.0.0', port=80)
