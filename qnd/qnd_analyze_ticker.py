# Run with the following command from the parent company:
# $ python -m qnd.qnd_analyze_ticker

import asyncio

from app import config
from app.schemas import analyze_ticker as analyze_ticker_schemas
from app.services import analyze_ticker
from app.utils import ai


async def main():
    body = analyze_ticker_schemas.AnalyzeTickerRequest(
        ticker="MSFT",
        quick_mode=True,
        intent="Should I buy, hold or sell in the coming week?",
        scenario="",
    )

    task_id = "ANALYZE_TICKER_ANALYZE_QUICK" if body.quick_mode else "ANALYZE_TICKER_ANALYZE"
    analyze_client = config.ai_task_settings.get_ai_client(task_id)
    analyze_task = config.ai_task_settings.tasks.get(task_id)

    asset = await analyze_ticker.fetch_asset_snapshot(body.ticker)
    print("Asset: ", asset.model_dump_json(indent=2))

    analysis_result = await ai.execute_task_prompt(
        analyze_client,
        analyze_task,
        analyze_ticker.build_research_prompt(body, asset),
        response_json_schema=analyze_ticker.response_schema(),
        schema_name="analyze_ticker_research",
    )
    print("AI Analysis Result: ", analysis_result.completion)

    research = analyze_ticker.parse_research(
        analysis_result.completion,
        request=body,
        asset=asset,
    )
    print("Parsed Research: ", research.model_dump_json(indent=2))

    payload = analyze_ticker.build_payload(asset, research)
    print("Final Payload: ", payload.model_dump_json(indent=2))


asyncio.run(main())
