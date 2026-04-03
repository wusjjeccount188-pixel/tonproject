import asyncio
import logging
import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# Updated imports for tonutils 2.x
from tonutils.client import ToncenterV3Client
from tonutils.dns import DNS
from tonutils.utils.address import Address
from tonutils.wallet import WalletV4R2

# Load environment variables
load_dotenv()

# ================= CONFIG =================
IS_TESTNET = os.getenv("IS_TESTNET", "false").lower() == "true"
TONCENTER_API_KEY = os.getenv("TONCENTER_API_KEY")

if not TONCENTER_API_KEY:
    raise RuntimeError("TONCENTER_API_KEY environment variable is required! Add it in Railway Variables.")

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Initialize client
client = ToncenterV3Client(
    api_key=TONCENTER_API_KEY,
    is_testnet=IS_TESTNET,
    rps=5,
    max_retries=3
)

app = FastAPI(title="My TON API (Pixy Style)")

# ================= MODELS =================
class TonBuyRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32, description="Telegram username without @")
    amount: float = Field(..., gt=0, description="Amount in TON")
    seed: str = Field(..., description="12 or 24 word mnemonic seed phrase")
    order_id: Optional[str] = None

class TonTransferRequest(BaseModel):
    to_address: str = Field(..., description="Recipient TON address (UQ or EQ format)")
    amount: float = Field(..., gt=0)
    seed: str = Field(...)
    comment: Optional[str] = Field(None, max_length=120)
    order_id: Optional[str] = None

class ApiResponse(BaseModel):
    ok: bool
    message: str
    error: Optional[str] = None
    order_id: Optional[str] = None
    tx_hash: Optional[str] = None
    recipient: Optional[str] = None

# ================= HELPERS =================
async def get_wallet_from_seed(seed: str):
    words = seed.strip().split()
    if len(words) not in (12, 24):
        raise ValueError("INVALID_MNEMONIC: Seed must be 12 or 24 words")
    try:
        wallet = WalletV4R2.from_mnemonic(client, words)  # Simplified in newer versions
        return wallet
    except Exception as e:
        err = str(e).lower()
        if "mnemonic" in err or "invalid" in err:
            raise ValueError("INVALID_MNEMONIC: Seed phrase is invalid")
        raise ValueError(f"WALLET_CREATION_FAILED: {str(e)}")

async def check_balance(wallet, required_amount: float):
    try:
        balance_nano = await wallet.get_balance()
        balance = balance_nano / 1_000_000_000
        estimated_fee = 0.05
        if balance < (required_amount + estimated_fee):
            raise ValueError(f"INSUFFICIENT_FUNDS: Need at least {required_amount + estimated_fee:.3f} TON (you have {balance:.3f} TON)")
        return balance
    except Exception:
        logger.warning("Balance check failed (continuing with caution)")
        return None

async def resolve_username_to_address(username: str) -> str:
    domain = f"{username.lower().strip()}.t.me"
    dns = DNS(client)
    try:
        resolved = await dns.resolve(domain)
        if resolved and resolved.address:
            addr = Address(resolved.address)
            return addr.to_str(is_user_friendly=True)
        raise ValueError("NO_DNS_RECORD")
    except Exception as e:
        logger.warning(f"DNS resolve failed for {domain}: {e}")
        raise ValueError(f"CANNOT_RESOLVE: @{username} has no .t.me DNS record (only Fragment NFT usernames usually work)")

# ================= ENDPOINTS =================
@app.get("/health")
async def health():
    return {"ok": True, "status": "running", "testnet": IS_TESTNET}

@app.post("/ton/buy", response_model=ApiResponse)
async def ton_buy(req: TonBuyRequest):
    try:
        logger.info(f"Buy request: @{req.username} | {req.amount} TON | Order: {req.order_id}")

        wallet = await get_wallet_from_seed(req.seed)
        await check_balance(wallet, req.amount)

        recipient = await resolve_username_to_address(req.username)

        comment = f"Topup via API | Order: {req.order_id or 'N/A'}"
        tx_hash = await wallet.transfer(
            destination=recipient,
            amount=req.amount,
            comment=comment
        )

        logger.info(f"Success buy: @{req.username} | tx={tx_hash}")
        return ApiResponse(
            ok=True,
            message=f"Successfully sent {req.amount} TON to @{req.username}",
            tx_hash=tx_hash,
            recipient=recipient,
            order_id=req.order_id
        )

    except ValueError as ve:
        error_code = str(ve).split(":")[0] if ":" in str(ve) else "VALIDATION_ERROR"
        return ApiResponse(ok=False, message=str(ve), error=error_code, order_id=req.order_id)
    except Exception as e:
        logger.error(f"Unexpected error in /ton/buy: {e}", exc_info=True)
        return ApiResponse(ok=False, message="INTERNAL_ERROR: Please try again later", error="INTERNAL_ERROR")

@app.post("/ton/transfer", response_model=ApiResponse)
async def ton_transfer(req: TonTransferRequest):
    try:
        logger.info(f"Transfer request: {req.to_address} | {req.amount} TON")

        wallet = await get_wallet_from_seed(req.seed)
        await check_balance(wallet, req.amount)

        try:
            Address(req.to_address)
        except Exception:
            raise ValueError("INVALID_ADDRESS: Wrong TON address format")

        comment = req.comment or f"Transfer via API | Order: {req.order_id or 'N/A'}"
        tx_hash = await wallet.transfer(
            destination=req.to_address,
            amount=req.amount,
            comment=comment
        )

        logger.info(f"Success transfer | tx={tx_hash}")
        return ApiResponse(
            ok=True,
            message=f"Successfully sent {req.amount} TON",
            tx_hash=tx_hash,
            recipient=req.to_address,
            order_id=req.order_id
        )

    except ValueError as ve:
        error_code = str(ve).split(":")[0] if ":" in str(ve) else "VALIDATION_ERROR"
        return ApiResponse(ok=False, message=str(ve), error=error_code, order_id=req.order_id)
    except Exception as e:
        logger.error(f"Unexpected error in /ton/transfer: {e}", exc_info=True)
        return ApiResponse(ok=False, message="INTERNAL_ERROR: Please try again later", error="INTERNAL_ERROR")

# ================= RUN =================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
