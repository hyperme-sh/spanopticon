"""Separate-process HTTP demonstration. Test credentials and no live funds."""
from contextlib import asynccontextmanager
from dataclasses import asdict
import json
import os
from pathlib import Path
from fastapi import FastAPI,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from controlplane_billing.settlement import SettlementIntent,SignedSettlementIntent
from .adapter import Adapter,TestProcessor,test_verifier
from .durable import DurableTestProcessor

class IntentRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    wallet:str
    amount:int=Field(gt=0)
    asset:str
    network:str
    idempotency_key:str
    signer:str
    signature_hex:str


def create_app():
    @asynccontextmanager
    async def lifespan(app):
        credentials=json.loads(Path(os.environ['SETTLEMENT_TEST_KEYS_FILE']).read_text())
        keys={(row['signer'],row['wallet']):bytes.fromhex(row['key_hex']) for row in credentials}
        ledger=os.environ.get('SETTLEMENT_TEST_LEDGER_FILE')
        processor=DurableTestProcessor(ledger) if ledger else TestProcessor()
        app.state.adapter=Adapter(processor,test_verifier(keys))
        app.state.durable=bool(ledger)
        yield
    app=FastAPI(lifespan=lifespan)
    @app.get('/health/ready')
    def ready():return {'ready':True,'processor':'test-double','live_funds':False,
                        'durable':app.state.durable}
    @app.post('/settlements')
    async def settle(body:IntentRequest):
        try:
            intent=SettlementIntent(body.wallet,body.amount,body.asset,body.network,body.idempotency_key)
            signed=SignedSettlementIntent(intent,body.signer,bytes.fromhex(body.signature_hex))
            return asdict(await app.state.adapter.settle(signed))
        except PermissionError as exc:
            raise HTTPException(403,str(exc)) from exc
        except (ValueError,TypeError) as exc:
            raise HTTPException(409,str(exc)) from exc
    return app
