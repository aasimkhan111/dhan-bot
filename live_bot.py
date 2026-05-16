import os
import json
import subprocess
import traceback
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

EC2_IP = "65.0.80.107"

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
    
    # ── STEP 2: Git Add + Commit ──
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
    
    # ── STEP 4: EC2 Sync ──
    if os.name == 'nt':
        # Running on LOCAL Windows → trigger remote EC2 pull
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
    else:
        # Running ON EC2 itself → config is already saved locally, no pull needed
        print("✅ [Step 4/4] Running on EC2 — config already live on this server!")
        steps.append({"step": "EC2 Live Reload", "status": "success", "detail": "Config applied instantly (running on EC2)"})
    
    return steps

# Initial sync with Dhan Master Scrip List
print("Syncing with Dhan Master Scrip List...")
df = pd.read_csv(SCRIP_URL, low_memory=False)

def get_security_id(symbol, price=0, option_type=None, manual_strike=None):
    try:
        symbol = symbol.upper()
        # --- DYNAMIC ATM/ITM LOGIC ---
        if "-ATM" in symbol or "-ITM" in symbol:
            base = symbol.split("-ATM")[0].split("-ITM")[0]
            
            # Use manual strike if provided (for precise exits), else calculate from price
            if manual_strike:
                strike = float(manual_strike)
            else:
                if price == 0:
                    print(f"❌ Error: price=0 received for {symbol} request.")
                    return None, None
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

            print(f"🎯 Using {symbol} logic for {base}: Strike {strike}")
            
            # Find the option with this strike and nearest expiry
            match = df[(df['SEM_INSTRUMENT_NAME'] == 'OPTIDX') & 
                       (df['SEM_STRIKE_PRICE'].astype(float) == float(strike)) &
                       (df['SEM_OPTION_TYPE'] == option_type) &
                       ((df['SM_SYMBOL_NAME'].str.contains(base, case=False, na=False)) | 
                        (df['SEM_CUSTOM_SYMBOL'].str.contains(base, case=False, na=False)) |
                        (df['SEM_TRADING_SYMBOL'].str.contains(base, case=False, na=False)))]
            
            if match.empty:
                print(f"⚠️ No strike {strike} found for {base}.")
                return None, None
            
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
                match = match.dropna(subset=['expiry_dt']).sort_values('expiry_dt')
            
            # Use the first active match
            row = match.iloc[0]
            sec_id = int(row['SEM_SMST_SECURITY_ID'])
            inst_name = str(row['SEM_INSTRUMENT_NAME'])
            final_symbol = str(row['SEM_TRADING_SYMBOL'])
            expiry = str(row['SEM_EXPIRY_DATE'])
            
            print(f"✅ Found: {final_symbol} | ID: {sec_id} | Expiry: {expiry}")
            return sec_id, inst_name
        else:
            print(f"❌ Symbol {symbol} not found.")
            return None, None
    except Exception as e:
        print(f"Lookup Error: {e}")
        return None, None

# --- API ROUTES ---

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(force=True, silent=True)
    config = load_config()
    
    if not data or data.get('secret') != config.get('SECRET_TOKEN', 'JunnarTrader2026'):
        print(f"🔴 403 ERROR! Unauthorized request.")
        return jsonify({"status": "error", "remarks": "Unauthorized"}), 403

    symbol = data.get('symbol')
    price = float(data.get('price', 0))
    option_type = data.get('option_type', 'CE').upper()
    manual_strike = data.get('itm_strike')

    # Resolve exact Security ID and Instrument Type
    sec_id, inst_name = get_security_id(symbol, price, option_type, manual_strike)
    
    if not sec_id:
        return jsonify({"status": "error", "remarks": "Symbol not found"}), 400

    # FORCE CORRECT SEGMENT
    if inst_name in ['OPTIDX', 'OPTSTK', 'FUTIDX', 'FUTSTK']:
        exch_seg = dhan.NSE_FNO
    else:
        exch_seg = dhan.NSE

    order_type_str = data.get('order_type', 'MARKET').upper()
    side_str = data.get('side', 'BUY').upper()
    
    try:
        # Initialize Dhan client dynamically to always use latest credentials
        dhan_context = DhanContext(client_id=config['CLIENT_ID'], access_token=config['ACCESS_TOKEN'])
        dhan_live = dhanhq(dhan_context)
        
        dhan_order_type = dhan_live.MARKET
        final_price = 0.0
        
        # Fetch exact LTP from Dhan Data API to satisfy strict Limit requirements
        print(f"🔍 Fetching Precise LTP for {sec_id} via Data API...")
        try:
            seg_key = "NSE_FNO" if exch_seg == dhan_live.NSE_FNO else "NSE"
            securities = {seg_key: [int(sec_id)]}
            
            quote = dhan_live.ticker_data(securities)
            print(f"📊 Raw Ticker Response: {quote}")
            
            if isinstance(quote, dict) and quote.get('status') == 'success':
                outer_data = quote.get('data', {})
                inner_data = outer_data.get('data', {})
                seg_data = inner_data.get(seg_key, {})
                id_data = seg_data.get(str(sec_id), {})
                
                ltp = float(id_data.get('last_price', 0))
                if ltp > 0:
                    final_price = ltp
                    dhan_order_type = dhan_live.LIMIT
                    print(f"🎯 LTP Found: {final_price}. Changing to EXACT LIMIT order.")
                else:
                    print("⚠️ LTP was 0. Falling back to Market (Protection).")
            else:
                print("⚠️ Data API failed (Maybe not Subscribed). Falling back to Market (Protection).")
        except Exception as e:
            print(f"⚠️ Error fetching LTP: {e}. Falling back to Market.")
        
        print(f"🚀 Firing {dhan_order_type} order for ID: {sec_id} at price: {final_price}")

        # Place order using strictly verified params
        response = dhan_live.place_order(
            security_id=int(sec_id),
            exchange_segment=exch_seg,
            transaction_type=dhan_live.BUY if side_str == 'BUY' else dhan_live.SELL,
            quantity=int(data.get('quantity', 0)),
            order_type=dhan_order_type,
            product_type=dhan_live.MARGIN, 
            price=float(final_price),
            after_market_order=False 
        )
        
        print(f"📡 Dhan API Order Response: {response}")
        return jsonify(response), 200

    except Exception as e:
        error_details = traceback.format_exc()
        print(f"❌ Order Execution Error:\n{error_details}")
        return jsonify({"error": str(e), "details": error_details}), 500

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

            header h1 {
                font-family: 'Outfit', sans-serif;
                font-size: 3.5rem;
                font-weight: 800;
                letter-spacing: -0.06em;
                background: linear-gradient(135deg, #fff 20%, var(--secondary) 50%, var(--primary) 100%);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                margin-bottom: 0.2rem;
                filter: drop-shadow(0 0 20px rgba(121, 40, 202, 0.3));
            }

            header p {
                font-family: 'Outfit', sans-serif;
                color: var(--text-dim);
                font-size: 1.1rem;
                font-weight: 400;
                letter-spacing: 0.2em;
                text-transform: uppercase;
                opacity: 0.8;
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
        </style>
    </head>
    <body>
        <header>
            <h1>REGAL ALGO</h1>
            <p>powered by Duocore Softwares</p>
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
        </div>

        <footer>
            &copy; 2026 Duocore Softwares | Regal Algo Engine v4.6
        </footer>

        <div class="toast" id="toast-notif">
            <span id="toast-text"></span>
        </div>

        <script>
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
                } catch (e) {
                    showToast('Failed to sync vault.', 'error');
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
                logArea.innerHTML = `<span style="color:var(--secondary)">[${now}]</span> 🚀 <span style="color:#fff; font-weight:bold;">INITIALIZING QUANT DEPLOYMENT...</span>\\n`;
                logArea.innerHTML += `<span style="color:#64748b">---------------------------------------------------</span>\\n`;
                logArea.scrollTop = logArea.scrollHeight;

                const payload = {
                    CLIENT_ID: document.getElementById('client-id').value.trim(),
                    ACCESS_TOKEN: document.getElementById('access-token').value.trim(),
                    SECRET_TOKEN: document.getElementById('secret-token').value.trim()
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
                            text += `<span style="color:${color}">${icon} Step ${i+1}: ${step.step}</span>\\n`;
                            text += `   <span style="color:#64748b">↳ ${step.detail}</span>\\n\\n`;
                        });
                        const endTime = new Date().toLocaleTimeString();
                        text += `<span style="color:#64748b">---------------------------------------------------</span>\\n`;
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

            window.onload = () => {
                fetchConfig();
                setInterval(fetchLogs, 2000);
            };
        </script>
    </body>
    </html>
    """

                }
            }

            // Pull live logs
            async function fetchLogs() {
                try {
                    const res = await fetch('/api/logs');
                    const data = await res.json();
                    const logArea = document.getElementById('terminal-logs');
                    
                    // Keep scroll at bottom if already near bottom
                    const isScrolledToBottom = logArea.scrollHeight - logArea.clientHeight <= logArea.scrollTop + 50;
                    
                    logArea.innerText = data.logs || 'No logs generated yet.';
                    
                    if (isScrolledToBottom) {
                        logArea.scrollTop = logArea.scrollHeight;
                    }
                } catch(e) {
                    // Fail silently for background polls
                }
            }

            // Initial loads & setup intervals
            window.onload = () => {
                fetchConfig();
                fetchLogs();
                // Refresh logs every 2 seconds
                setInterval(fetchLogs, 2000);
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
