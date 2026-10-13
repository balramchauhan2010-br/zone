import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from api.main import app

IST = ZoneInfo("Asia/Kolkata")


def test_health_and_unavailable_data_endpoints_have_explicit_statuses():
    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            health = await client.get("/api/health")
            news = await client.get("/api/news")
            delivery = await client.get("/api/delivery/TEST")
            global_markets = await client.get("/api/global/markets")
        return health, news, delivery, global_markets

    health, news, delivery, global_markets = asyncio.run(exercise())
    assert health.status_code == 200
    assert "received_at" in health.json()
    assert "source_timestamp" in health.json()
    assert "dhan" in health.json()["data"]
    assert news.json()["status"] == "UNAVAILABLE"
    assert delivery.json()["status"] == "UNAVAILABLE"
    assert global_markets.json()["status"] == "UNAVAILABLE"


def test_global_csv_upload_is_marked_unverified_not_live():
    start = datetime(2026, 10, 12, 9, 15, tzinfo=IST)
    lines = ["timestamp,open,high,low,close,volume"]
    for idx in range(40):
        stamp = start + timedelta(minutes=idx)
        base = 100 + idx * 0.01
        lines.append(f"{stamp.isoformat()},{base},{base + 0.5},{base - 0.5},{base + 0.1},100")
    csv_bytes = ("\n".join(lines) + "\n").encode()

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/global/zones/upload",
                params={"symbol": "BTCUSD", "timeframe": "5m"},
                files={"file": ("actual_user_file.csv", csv_bytes, "text/csv")},
            )

    response = asyncio.run(exercise())
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "USER_PROVIDED"
    assert payload["data"]["source"] == "USER_UPLOADED_CSV"
    assert payload["data"]["data_quality_status"] == "UNVERIFIED_USER_SUPPLIED_DATA"
