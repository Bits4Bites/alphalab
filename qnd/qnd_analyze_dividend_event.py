# Run with the following command from the parent company:
# $ python -m qnd.qnd_analyze_dividend_event

import asyncio
import datetime
import decimal

from app import config
from app.schemas import dividend_event as dividend_event_schemas
from app.services import analyze_ticker, dividend_event
from app.utils import ai


async def main():
    body = dividend_event_schemas.DividendEventRequest(
        ticker="ASX:CBA",
        dividend_amount=decimal.Decimal(2.700),
        ex_dividend_date=datetime.date(2026, 8, 19),
        current_price=decimal.Decimal(167.17),
        tax_situation="franking_eligible",
        holding_period="already_holding",
        additional_notes="Should I capture the dividend or buy the post-dividend-discount?",
    )

    asset = await analyze_ticker.fetch_asset_snapshot(body.ticker)
    print("Asset: ", asset.model_dump_json(indent=2))
    market = await dividend_event.fetch_market_snapshot(asset, body)
    print("Market: ", market.model_dump_json(indent=2))

    task_id = "DIVIDEND_EVENT_ANALYZE"
    analyze_client = config.ai_task_settings.get_ai_client(task_id)
    analyze_task = config.ai_task_settings.tasks.get(task_id)
    analysis_result = await ai.execute_task_prompt(
        analyze_client,
        analyze_task,
        dividend_event.build_research_prompt(body, asset, market),
        response_json_schema=dividend_event.response_schema(),
        schema_name="dividend_event_report",
    )
    print("AI Analysis Result: ", analysis_result.completion)

    report = dividend_event.parse_report(
        analysis_result.completion,
        request=body,
        asset=asset,
    )
    print("Parsed Report: ", report.model_dump_json(indent=2))

    payload = dividend_event.build_payload(asset, market, report)
    print("Final Payload: ", payload.model_dump_json(indent=2))


asyncio.run(main())
