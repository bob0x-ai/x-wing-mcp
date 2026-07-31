#!/usr/bin/env python3
"""
X-Wing OAuth 2.0 Setup Script (Headless VPS Edition) - v2.0

This script helps users obtain OAuth 2.0 Access Token and Refresh Token
for the X-Wing skill using the PKCE flow with PIN-based authorization.

Designed for headless VPS environments - no browser required on the server.

Usage:
    python oauth_setup.py

Requirements:
    - X_CLIENT_ID and X_CLIENT_SECRET set in ~/.hermes/.env
    - requests library installed
    - Optional: qrcode library for QR code generation
    - Optional: pyperclip library for clipboard copy

Headless Flow:
    1. Script generates authorization URL
    2. URL is displayed (and optionally copied to clipboard / shown as QR code)
    3. User opens URL in their local browser
    4. User authorizes app on X.com
    5. User copies the 7-digit PIN
    6. User pastes PIN in terminal
    7. Script exchanges PIN for tokens
    8. Tokens are saved to ~/.hermes/.env

Troubleshooting:
    If you get "Something went wrong" when opening the URL:
    1. Check your X app callback URL configuration
    2. Verify OAuth 2.0 is enabled in your app settings
    3. Try a different redirect_uri option
"""

import os
import sys
import secrets
import base64
import hashlib
import re
import subprocess
import json
import shutil
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlencode, quote, urlparse, parse_qs
from pathlib import Path

import requests

# Optional imports for enhanced UX
try:
    import qrcode
    QR_AVAILABLE = True
except ImportError:
    QR_AVAILABLE = False

try:
    import pyperclip
    CLIPBOARD_AVAILABLE = True
except ImportError:
    CLIPBOARD_AVAILABLE = False

# OAuth 2.0 Configuration
OAUTH_CONFIG = {
    'authorize_url': 'https://x.com/i/oauth2/authorize',
    'token_url': 'https://api.x.com/2/oauth2/token',
    'scope': 'tweet.read tweet.write users.read offline.access like.read like.write dm.read dm.write follows.write bookmark.write'
}

# Multiple redirect_uri options to try
# Priority order: most compatible first
REDIRECT_URI_OPTIONS = [
    {
        'uri': 'http://127.0.0.1',
        'name': 'Localhost',
        'description': 'Standard localhost redirect (recommended)',
        'pin_mode': False
    },
    {
        'uri': 'https://localhost',
        'name': 'HTTPS Localhost',
        'description': 'Secure localhost redirect',
        'pin_mode': False
    },
    {
        'uri': 'urn:ietf:wg:oauth:2.0:oob',
        'name': 'PIN-based (Legacy)',
        'description': 'Legacy PIN-based flow (may not work with new apps)',
        'pin_mode': True
    }
]


def load_env_file(env_path: Path) -> dict:
    """Load environment variables from file."""
    env_vars = {}
    if env_path.exists():
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    env_vars[key] = value.strip('"\'')
    return env_vars


def save_env_file(env_path: Path, env_vars: dict):
    """Save environment variables to file."""
    with open(env_path, 'w') as f:
        for key, value in env_vars.items():
            # Quote values that contain spaces
            if ' ' in str(value) and not (value.startswith('"') or value.startswith("'")):
                value = f'"{value}"'
            f.write(f"{key}={value}\n")


def generate_pkce_pair():
    """Generate PKCE code verifier and challenge."""
    # Generate code verifier (43-128 characters)
    code_verifier = base64.urlsafe_b64encode(
        secrets.token_bytes(32)
    ).decode('utf-8').rstrip('=')
    
    # Generate code challenge (SHA256 hash of verifier)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).decode('utf-8').rstrip('=')
    
    return code_verifier, code_challenge


def generate_state():
    """Generate random state parameter."""
    return secrets.token_urlsafe(16)


def normalize_scopes(scopes) -> str | None:
    """Convert a token scope payload to a stable space-delimited string."""
    if not scopes:
        return None
    if isinstance(scopes, str):
        return " ".join(scopes.split())
    if isinstance(scopes, (list, tuple, set)):
        return " ".join(str(scope).strip() for scope in scopes if str(scope).strip())
    return str(scopes).strip() or None


def build_authorization_url(client_id: str, code_challenge: str, state: str, 
                           redirect_uri: str) -> str:
    """Build OAuth 2.0 authorization URL."""
    params = {
        'response_type': 'code',
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'scope': OAUTH_CONFIG['scope'],
        'state': state,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256'
    }
    
    return f"{OAUTH_CONFIG['authorize_url']}?{urlencode(params, quote_via=quote)}"


def exchange_code_for_tokens(
    code: str,
    code_verifier: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str
) -> dict:
    """Exchange authorization code for access and refresh tokens."""
    data = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri,
        'code_verifier': code_verifier,
        'client_id': client_id
    }
    
    response = requests.post(
        OAUTH_CONFIG['token_url'],
        data=data,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        auth=(client_id, client_secret)
    )
    
    response.raise_for_status()
    return response.json()


def copy_to_clipboard(text: str) -> bool:
    """Copy text to clipboard using available methods."""
    # Try pyperclip first (cross-platform)
    if CLIPBOARD_AVAILABLE:
        try:
            pyperclip.copy(text)
            return True
        except Exception:
            pass
    
    # Try xclip (Linux)
    try:
        subprocess.run(['xclip', '-selection', 'clipboard'], 
                      input=text.encode(), check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    
    # Try xsel (Linux)
    try:
        subprocess.run(['xsel', '--clipboard', '--input'], 
                      input=text.encode(), check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    
    return False


def display_qr_code(url: str) -> bool:
    """Display URL as QR code in terminal."""
    if not QR_AVAILABLE:
        return False
    
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=1,
            border=1,
        )
        qr.add_data(url)
        qr.make(fit=True)
        
        # Try to use terminal-based QR code
        try:
            qr.print_ascii(invert=True)
            return True
        except:
            # Fallback to compact terminal output
            qr.make(fit=True)
            qr.print_tty()
            return True
    except Exception:
        return False


def validate_code(code: str) -> bool:
    """Validate authorization code format."""
    if not code:
        return False
    
    # Remove any whitespace
    code = code.strip()
    
    # Code should be reasonably long (typically 40+ characters)
    if len(code) < 10:
        return False
    
    return True


def extract_code_from_url(url: str) -> tuple:
    """Extract authorization code from callback URL."""
    try:
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        
        if 'code' in query_params:
            return query_params['code'][0], None
        elif 'error' in query_params:
            error = query_params['error'][0]
            error_desc = query_params.get('error_description', [''])[0]
            return None, f"{error}: {error_desc}"
    except Exception as e:
        return None, str(e)
    
    return None, "No code found in URL"


def _extract_env_value(lines: list[str], key: str) -> str | None:
    """Return the current value for a key in .env lines."""
    pattern = re.compile(rf"^\s*#?\s*{re.escape(key)}=(.*)$")
    for line in lines:
        match = pattern.match(line.strip())
        if not match:
            continue
        value = match.group(1).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        return value
    return None


def _write_env_file_atomically(env_path: Path, lines: list[str]) -> Path:
    """Back up and atomically replace a .env file."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    backup_path = env_path.with_name(f"{env_path.name}.bak.{timestamp}")
    shutil.copy2(env_path, backup_path)

    original_stat = env_path.stat()
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            dir=env_path.parent,
            prefix=f".{env_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            os.chmod(temp_path, original_stat.st_mode)
            temp_file.writelines(lines)
            temp_file.flush()
            os.fsync(temp_file.fileno())

        os.replace(temp_path, env_path)

        try:
            dir_fd = os.open(env_path.parent, os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except Exception:
        if temp_path and temp_path.exists():
            temp_path.unlink()
        raise

    return backup_path


def update_env_tokens(
    env_path: Path,
    access_token: str,
    refresh_token: str | None = None,
    scopes: str | None = None,
):
    """Update or add token entries in .env file."""
    if not env_path.exists():
        print(f"❌ Error: {env_path} does not exist")
        sys.exit(1)
    
    # Read existing content
    with open(env_path, 'r') as f:
        lines = f.readlines()

    existing_refresh_token = (
        _extract_env_value(lines, 'X_OAUTH2_REFRESH_TOKEN')
        or _extract_env_value(lines, 'X_REFRESH_TOKEN')
    )
    effective_refresh_token = refresh_token or existing_refresh_token
    existing_scopes = (
        _extract_env_value(lines, 'X_OAUTH2_SCOPES')
        or _extract_env_value(lines, 'X_SCOPES')
    )
    effective_scopes = scopes or existing_scopes

    # Update canonical X_OAUTH2_* entries and legacy X_* aliases together.
    token_vars = {
        'X_OAUTH2_ACCESS_TOKEN': access_token,
        'X_ACCESS_TOKEN': access_token,
    }
    if effective_refresh_token:
        token_vars.update({
            'X_OAUTH2_REFRESH_TOKEN': effective_refresh_token,
            'X_REFRESH_TOKEN': effective_refresh_token,
        })
    if effective_scopes:
        token_vars.update({
            'X_OAUTH2_SCOPES': effective_scopes,
            'X_SCOPES': effective_scopes,
        })
    
    updated_lines = []
    existing_keys = set()
    
    for line in lines:
        stripped = line.strip()
        # Check if this line is one of our token vars
        for key in token_vars:
            if stripped.startswith(f"{key}=") or stripped.startswith(f"# {key}="):
                existing_keys.add(key)
                # Replace with new value
                updated_lines.append(f"{key}={token_vars[key]}\n")
                break
        else:
            updated_lines.append(line)
    
    # Add any missing token vars
    for key, value in token_vars.items():
        if key not in existing_keys:
            updated_lines.append(f"{key}={value}\n")
    
    backup_path = _write_env_file_atomically(env_path, updated_lines)
    print(f"✓ Backup saved to {backup_path}")


def print_header():
    """Print script header."""
    print()
    print("=" * 60)
    print("🚀 X-Wing OAuth 2.0 Setup (Headless Mode) - v2.0")
    print("=" * 60)
    print()


def print_troubleshooting_guide():
    """Print troubleshooting information."""
    print()
    print("=" * 60)
    print("🔧 Troubleshooting Guide")
    print("=" * 60)
    print()
    print("If you see 'Something went wrong' when opening the URL:")
    print()
    print("1. Check your X Developer App settings:")
    print("   - Go to https://developer.x.com/en/portal/dashboard")
    print("   - Select your app")
    print("   - Click 'Edit' under 'User authentication settings'")
    print()
    print("2. Verify these settings:")
    print("   ✅ App permissions: 'Read and Write' (not 'Read-only')")
    print("   ✅ OAuth 2.0: ENABLED")
    print("   ✅ Type of App: 'Native app' or 'Web App'")
    print()
    print("3. Check Callback URLs (case-sensitive!):")
    print("   Must include EXACTLY one of these:")
    print("     • http://127.0.0.1")
    print("     • https://localhost")  
    print("     • urn:ietf:wg:oauth:2.0:oob (legacy)")
    print()
    print("4. Common mistakes:")
    print("   ❌ Using 'http://localhost' instead of 'http://127.0.0.1'")
    print("   ❌ Trailing slash in callback URL (/)")
    print("   ❌ App configured for OAuth 1.0a instead of OAuth 2.0")
    print("   ❌ Wrong Client ID or Client Secret")
    print()
    print("5. If it still doesn't work:")
    print("   - Try creating a new X app with OAuth 2.0 from the start")
    print("   - Ensure your app has 'Elevated' or 'Basic' access level")
    print()
    print("=" * 60)


def diagnose_error(auth_url: str, error_response: str = None):
    """Diagnose common OAuth errors."""
    print()
    print("🔍 Error Diagnostics")
    print("-" * 60)
    
    # Parse the URL to extract parameters
    parsed = urlparse(auth_url)
    params = parse_qs(parsed.query)
    
    client_id = params.get('client_id', [''])[0]
    redirect_uri = params.get('redirect_uri', [''])[0]
    
    print(f"Client ID: {client_id[:20]}..." if len(client_id) > 20 else f"Client ID: {client_id}")
    print(f"Redirect URI: {redirect_uri}")
    print(f"Scope: {params.get('scope', [''])[0]}")
    print()
    
    if error_response:
        print(f"Error response: {error_response}")
        print()
    
    print("Possible causes:")
    
    if redirect_uri == 'urn:ietf:wg:oauth:2.0:oob':
        print("  • PIN-based flow (urn:ietf:wg:oauth:2.0:oob) may not be")
        print("    supported for your app. Try using http://127.0.0.1")
        print("    as the callback URL instead.")
    
    print("  • Callback URL mismatch between URL and X Developer Console")
    print("  • OAuth 2.0 not enabled in app settings")
    print("  • App permissions set to 'Read-only' instead of 'Read and Write'")
    print()


def select_redirect_uri() -> dict:
    """Let user select which redirect URI to use."""
    print()
    print("Select authorization method:")
    print("-" * 60)
    
    for i, option in enumerate(REDIRECT_URI_OPTIONS, 1):
        print(f"{i}. {option['name']}")
        print(f"   URI: {option['uri']}")
        print(f"   {option['description']}")
        print()
    
    while True:
        try:
            choice = input("Enter choice (1-3) [1]: ").strip()
            if not choice:
                choice = "1"
            
            idx = int(choice) - 1
            if 0 <= idx < len(REDIRECT_URI_OPTIONS):
                return REDIRECT_URI_OPTIONS[idx]
            else:
                print(f"Please enter a number between 1 and {len(REDIRECT_URI_OPTIONS)}")
        except ValueError:
            print("Please enter a valid number")


def main():
    """Main setup flow for headless VPS environment."""
    print_header()
    
    # Determine paths
    repo_root = Path(__file__).resolve().parent
    env_path = Path(os.getenv("X_WING_ENV_PATH", str(repo_root / ".env")))
    
    # Check if .env exists
    if not env_path.exists():
        print(f"❌ Error: {env_path} does not exist")
        print("Please create it with X_CLIENT_ID and X_CLIENT_SECRET")
        print()
        print(f"Example {env_path}:")
        print("  X_CLIENT_ID=your_client_id_here")
        print("  X_CLIENT_SECRET=your_client_secret_here")
        sys.exit(1)
    
    # Load credentials
    env_vars = load_env_file(env_path)
    client_id = env_vars.get('X_OAUTH2_CLIENT_ID') or env_vars.get('X_CLIENT_ID')
    client_secret = env_vars.get('X_OAUTH2_CLIENT_SECRET') or env_vars.get('X_CLIENT_SECRET')
    
    if not client_id or not client_secret:
        print(f"❌ Error: X_OAUTH2_CLIENT_ID/X_CLIENT_ID or X_OAUTH2_CLIENT_SECRET/X_CLIENT_SECRET not found in {env_path}")
        print(f"\nPlease add these lines to {env_path}:")
        print("  X_OAUTH2_CLIENT_ID=your_client_id")
        print("  X_OAUTH2_CLIENT_SECRET=your_client_secret")
        sys.exit(1)
    
    print(f"✓ Credentials loaded from {env_path}")
    print(f"  Client ID: {client_id[:20]}..." if len(client_id) > 20 else f"  Client ID: {client_id}")
    print()
    
    # Let user choose redirect URI
    redirect_option = select_redirect_uri()
    redirect_uri = redirect_option['uri']
    is_pin_mode = redirect_option['pin_mode']
    
    print(f"✓ Using redirect URI: {redirect_uri}")
    print()
    
    # Generate PKCE parameters
    code_verifier, code_challenge = generate_pkce_pair()
    state = generate_state()
    
    # Build authorization URL
    auth_url = build_authorization_url(client_id, code_challenge, state, redirect_uri)
    
    # Step 1: Display authorization URL
    print("📱 Step 1: Open this URL in your browser:")
    print("=" * 60)
    print(auth_url)
    print("=" * 60)
    print()
    
    # Try to copy to clipboard
    clipboard_copied = copy_to_clipboard(auth_url)
    if clipboard_copied:
        print("✓ URL copied to clipboard!")
        print()
    
    # Display QR code if available
    if QR_AVAILABLE:
        print("📷 Or scan this QR code with your phone:")
        print()
        qr_displayed = display_qr_code(auth_url)
        if qr_displayed:
            print()
    
    # Instructions
    print("💡 Instructions:")
    if is_pin_mode:
        print("   1. Open the URL above in a browser on your local machine")
        print("   2. Log in to X (Twitter) if prompted")
        print("   3. Authorize the X-Wing app")
        print("   4. Copy the 7-digit PIN displayed by X")
        print()
        print("⏰ Note: The authorization PIN expires after a few minutes.")
    else:
        print("   1. Open the URL above in a browser on your local machine")
        print("   2. Log in to X (Twitter) if prompted")
        print("   3. Authorize the X-Wing app")
        print("   4. You will be redirected to a localhost URL")
        print("   5. Copy the FULL redirect URL from your browser's address bar")
        print("      (It will look like: http://127.0.0.1/?code=...&state=...)")
        print()
        print("⏰ Note: The authorization code expires after a few minutes.")
    print()
    
    # Step 2: Get code/PIN from user
    try:
        if is_pin_mode:
            user_input = input("🔑 Step 2: Paste the PIN from X and press Enter: ").strip()
            auth_code = user_input
        else:
            user_input = input("🔑 Step 2: Paste the FULL redirect URL and press Enter: ").strip()
            # Try to extract code from URL
            auth_code, error = extract_code_from_url(user_input)
            if error:
                # User might have just pasted the code directly
                if validate_code(user_input):
                    auth_code = user_input
                else:
                    print(f"❌ Error: Could not extract code from URL: {error}")
                    diagnose_error(auth_url, error)
                    print_troubleshooting_guide()
                    sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n❌ Setup cancelled by user")
        sys.exit(0)
    
    # Validate code format
    if not auth_code:
        print("❌ Error: No authorization code entered")
        sys.exit(1)
    
    if not validate_code(auth_code):
        print("⚠️  Warning: Authorization code format looks unusual")
        print(f"   You entered: {auth_code[:50]}..." if len(auth_code) > 50 else f"   You entered: {auth_code}")
        confirm = input("   Continue anyway? (y/n): ").strip().lower()
        if confirm != 'y':
            print("\n❌ Setup cancelled")
            sys.exit(0)
    
    print()
    
    # Step 3: Exchange code for tokens
    print("⏳ Exchanging code for tokens...")
    
    try:
        token_response = exchange_code_for_tokens(
            code=auth_code,
            code_verifier=code_verifier,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri
        )
        
        access_token = token_response.get('access_token')
        refresh_token = token_response.get('refresh_token')
        
        if not access_token:
            print("❌ Error: Token response missing access_token")
            print(f"Response: {json.dumps(token_response, indent=2)}")
            sys.exit(1)
        
        # Show truncated tokens
        access_truncated = access_token[:20] + "..." if len(access_token) > 20 else access_token
        refresh_truncated = refresh_token[:20] + "..." if refresh_token and len(refresh_token) > 20 else refresh_token
        
        print(f"✓ Access Token: {access_truncated}")
        if refresh_token:
            print(f"✓ Refresh Token: {refresh_truncated}")
        else:
            print("⚠️  No refresh token received (normal for some configurations)")
        print()
        
        # Step 4: Save to .env
        print(f"💾 Saving tokens to {env_path}...")
        granted_scopes = normalize_scopes(token_response.get('scope')) or OAUTH_CONFIG['scope']
        update_env_tokens(env_path, access_token, refresh_token, granted_scopes)
        print("✓ Tokens saved successfully!")
        print()
        
        # Success message
        print("=" * 60)
        print("🎉 Setup Complete!")
        print("=" * 60)
        print()
        print("You can now use the X-Wing skill!")
        print("Your tokens have been saved and will be used automatically.")
        print()
        print("Test it with:")
        print("  python scripts/x_client.py timeline --limit 5")
        print()
        
    except requests.exceptions.HTTPError as e:
        print(f"❌ Error: Failed to exchange code for tokens")
        if e.response is not None:
            try:
                error_data = e.response.json()
                error_msg = error_data.get('error_description', error_data.get('error', str(e)))
                print(f"   Details: {error_msg}")
                print()
                
                if 'invalid_client' in str(error_msg).lower():
                    print("💡 Your Client ID or Client Secret may be incorrect.")
                    print("   Double-check them in your X Developer Portal.")
                elif 'invalid_grant' in str(error_msg).lower():
                    print("💡 The authorization code may have expired.")
                    print("   Please re-run this script to get a new one.")
                elif 'redirect_uri' in str(error_msg).lower():
                    print("💡 Redirect URI mismatch!")
                    print(f"   You used: {redirect_uri}")
                    print("   This must EXACTLY match what's in your X app settings.")
                else:
                    diagnose_error(auth_url, str(error_msg))
                    print_troubleshooting_guide()
                    
            except:
                print(f"   Response: {e.response.text}")
                print_troubleshooting_guide()
        sys.exit(1)
    
    except requests.exceptions.RequestException as e:
        print(f"❌ Error: Network error during token exchange")
        print(f"   Details: {e}")
        sys.exit(1)
    
    except Exception as e:
        print(f"❌ Error: Unexpected error")
        print(f"   Details: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
