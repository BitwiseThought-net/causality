#!/usr/bin/env bash

# Exit immediately if a command exits with a non-zero status
set -e

ENV_FILE=".env"

echo "🔍 Checking and installing missing system prerequisites..."

# 1. Detect Package Manager & Operating System
if [[ "$OSTYPE" == "darwin"* ]]; then
    if ! command -v brew &> /dev/null; then
        echo "❌ Homebrew is required on macOS but was not found."
        echo "Please install it from https://brew.sh and run this script again."
        exit 1
    fi
    INSTALL_CMD="brew install"
    UPDATE_CMD="brew update"
    PKG_PYTHON="python"
    PKG_PIP="" 
    PKG_OPENSSL="openssl"
    PKG_CURL="curl"
    USE_SUDO=""
elif [ -f /etc/debian_version ]; then
    INSTALL_CMD="apt-get install -y"
    UPDATE_CMD="apt-get update"
    PKG_PYTHON="python3"
    PKG_PIP="python3-pip"
    PKG_OPENSSL="openssl"
    PKG_CURL="curl"
    USE_SUDO="sudo"
    
    # DYNAMIC PYTHON VERSION DETECTOR FOR DEBIAN/UBUNTU
    PY_VER_CMD=$(python3 -c "import sys; print(f'python{sys.version_info.major}.{sys.version_info.minor}')")
    PKG_VENV="${PY_VER_CMD}-venv"
elif [ -f /etc/redhat-release ]; then
    if command -v dnf &> /dev/null; then
        INSTALL_CMD="dnf install -y"
    else
        INSTALL_CMD="yum install -y"
    fi
    UPDATE_CMD=""
    PKG_PYTHON="python3"
    PKG_PIP="python3-pip"
    PKG_VENV=""
    PKG_OPENSSL="openssl"
    PKG_CURL="curl"
    USE_SUDO="sudo"
elif [ -f /etc/alpine-release ]; then
    INSTALL_CMD="apk add --no-cache"
    UPDATE_CMD=""
    PKG_PYTHON="python3"
    PKG_PIP="py3-pip"
    PKG_VENV="python3-dev"
    PKG_OPENSSL="openssl"
    PKG_CURL="curl"
    USE_SUDO=""
else
    echo "⚠️ Unknown OS type. Skipping automated prerequisite install."
    echo "Please ensure python3, pip3, openssl, and curl are installed manually."
fi

# 2. Function to check and install missing tools
ensure_dependency() {
    local cmd_name=$1
    local package_names=$2

    if [ -z "$package_names" ]; then
        return
    fi

    if ! command -v "$cmd_name" &> /dev/null; then
        echo "📥 '$cmd_name' or its package wrapper is missing. Installing..."
        if [ -n "$UPDATE_CMD" ]; then
            echo "🔄 Refreshing package lists..."
            $USE_SUDO $UPDATE_CMD &> /dev/null || true
            UPDATE_CMD="" 
        fi
        $USE_SUDO $INSTALL_CMD $package_names
        echo "✅ '$cmd_name' successfully installed."
    else
        echo "✔️ '$cmd_name' is already available."
    fi
}

# 3. Fire dependency assertions sequentially
if [ -n "$INSTALL_CMD" ]; then
    ensure_dependency "python3" "$PKG_PYTHON"
    ensure_dependency "pip3" "$PKG_PIP"
    
    # ACCURATE VENV DETECTOR FOR DEBIAN/UBUNTU
    # Tries to actually execute a test venv setup. If ensurepip error fires, it captures it.
    if [ -f /etc/debian_version ] && [ -n "$PKG_VENV" ]; then
        # Create a temp directory name
        TEST_VENV_DIR=$(mktemp -d -t venv-test-XXXXXXXXXX)
        
        # Test if it actually works or throws the ensurepip missing error
        if ! python3 -m venv "$TEST_VENV_DIR" &> /dev/null; then
            echo "📥 Python venv module for $PY_VER_CMD is broken/missing. Installing $PKG_VENV..."
            if [ -n "$UPDATE_CMD" ]; then
                $USE_SUDO $UPDATE_CMD &> /dev/null || true
                UPDATE_CMD=""
            fi
            $USE_SUDO $INSTALL_CMD "$PKG_VENV"
            echo "✅ $PKG_VENV successfully installed."
        fi
        
        # Clean up the test directory completely
        rm -rf "$TEST_VENV_DIR"
    fi
    
    ensure_dependency "openssl" "$PKG_OPENSSL"
    ensure_dependency "curl" "$PKG_CURL"
fi

# 4. Generate fresh cryptographic values using openssl
echo "🔑 Crafting environmental tokens..."
GEN_WEBHOOK_SECRET=$(openssl rand -hex 32)
GEN_RABBIT_PASS=$(openssl rand -hex 12)

# Case A: File does not exist -> Create it with all fresh values
if [ ! -f "$ENV_FILE" ]; then
    cat << EOF > "$ENV_FILE"
WEBHOOK_SECRET=$GEN_WEBHOOK_SECRET
RABBITMQ_USER=admin
RABBITMQ_PASS=$GEN_RABBIT_PASS
EOF
    echo "✨ Created a fresh '$ENV_FILE' file with all secret variables populated!"

# Case B: File exists -> Update WEBHOOK_SECRET and add RabbitMQ fields if missing
else
    NEED_NEWLINE=false
    if [ -s "$ENV_FILE" ] && [ "$(tail -c 1 "$ENV_FILE")" != "" ]; then
        NEED_NEWLINE=true
    fi

    if grep -q "^WEBHOOK_SECRET=" "$ENV_FILE"; then
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s/^WEBHOOK_SECRET=.*/WEBHOOK_SECRET=$GEN_WEBHOOK_SECRET/" "$ENV_FILE"
        else
            sed -i "s/^WEBHOOK_SECRET=.*/WEBHOOK_SECRET=$GEN_WEBHOOK_SECRET/" "$ENV_FILE"
        fi
        echo "🔄 Updated 'WEBHOOK_SECRET' inside your existing '$ENV_FILE' file!"
    else
        [ "$NEED_NEWLINE" = true ] && echo "" >> "$ENV_FILE" && NEED_NEWLINE=false
        echo "WEBHOOK_SECRET=$GEN_WEBHOOK_SECRET" >> "$ENV_FILE"
        echo "➕ Appended missing 'WEBHOOK_SECRET' to your '$ENV_FILE' file!"
    fi

    if ! grep -q "^RABBITMQ_USER=" "$ENV_FILE"; then
        [ "$NEED_NEWLINE" = true ] && echo "" >> "$ENV_FILE" && NEED_NEWLINE=false
        echo "RABBITMQ_USER=admin" >> "$ENV_FILE"
        echo "➕ Appended missing 'RABBITMQ_USER' to your '$ENV_FILE' file!"
    fi

    if ! grep -q "^RABBITMQ_PASS=" "$ENV_FILE"; then
        [ "$NEED_NEWLINE" = true ] && echo "" >> "$ENV_FILE" && NEED_NEWLINE=false
        echo "RABBITMQ_PASS=$GEN_RABBIT_PASS" >> "$ENV_FILE"
        echo "➕ Appended missing 'RABBITMQ_PASS' to your '$ENV_FILE' file!"
    fi
fi

# 5. Automatically manage local isolated Virtual Environment
echo "📦 Reviewing local python test libraries..."

# Clean out any previously broken venv folders to ensure a pristine build
if [ -d ".venv" ]; then
    echo "🗑️ Clearing existing virtual environment to avoid sync conflicts..."
    rm -rf .venv
fi

echo "Creating a local isolated virtual environment in .venv/..."
python3 -m venv .venv

# Use the pip binary directly inside the venv to bypass PEP 668 system blockages
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install requests pika --quiet

echo "🏁 Setup script complete!"
echo "💡 To execute your test script on the host machine later, run: .venv/bin/python test_webhook.py"
echo "👉 You are now ready to run: docker compose up -d"

