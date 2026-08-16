import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from app import config, dependencies, templating
from app.schemas import dashboard as dashboard_schemas
from app.services import dashboard_analysis
from app.utils import ai

router = APIRouter(tags=["dashboard"])
TEMPLATE = "dashboard.html"


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user: dict = Depends(dependencies.get_current_user)) -> HTMLResponse:
    from app.services import market_news, sample_prompts

    prompts = await sample_prompts.get_random_sample_prompts(4)
    news = await market_news.get_market_news()
    ai_ideas = await market_news.get_ai_ideas(3)
    context = {"user": user, "sample_prompts": prompts, "market_news": news, "ai_ideas": ai_ideas}
    return templating.templates.TemplateResponse(request, TEMPLATE, context)


@router.post("/dashboard/stream")
async def dashboard_stream(
    body: dashboard_schemas.DashboardAnalysisRequest,
    _user: dict = Depends(dependencies.get_current_user),
) -> EventSourceResponse:
    async def event_generator():
        def progress(step: int, total: int, message: str):
            return json.dumps({"type": "progress", "step": step, "total": total, "message": message})

        def error(message: str):
            return json.dumps({"type": "error", "message": message})

        def result(content: str):
            return json.dumps({"type": "result", "content": content})

        total_steps = 4

        # Step 1: Prepare the validated request
        yield {"data": progress(1, total_steps, "Preparing your request...")}

        # Step 2: Plan the research request
        yield {"data": progress(2, total_steps, "Building analysis prompt...")}
        build_prompt_client = config.ai_task_settings.get_ai_client("DASHBOARD_BUILD_PROMPT")
        build_prompt_task = config.ai_task_settings.tasks.get("DASHBOARD_BUILD_PROMPT")
        if not build_prompt_client or not build_prompt_task:
            yield {"data": error("AI task 'DASHBOARD_BUILD_PROMPT' is not configured.")}
            return

        prompt_result = await ai.execute_task_prompt(
            build_prompt_client,
            build_prompt_task,
            dashboard_analysis.build_plan_prompt(body.intent),
            response_json_schema=dashboard_schemas.DashboardPlan.model_json_schema(),
            schema_name="dashboard_research_plan",
        )

        if not prompt_result.success:
            yield {"data": error(f"Failed to process your request: {prompt_result.error}")}
            return

        try:
            plan = dashboard_analysis.parse_plan_response(prompt_result.completion)
        except dashboard_analysis.DashboardPlanResponseError:
            yield {"data": error("The AI planner returned an invalid response. Please try again.")}
            return

        if plan.status == "rejected":
            yield {"data": error(f"Your request doesn't appear to be related to the stock market. {plan.reason}")}
            return

        # Step 3: Execute the prompt
        yield {"data": progress(3, total_steps, "Generating analysis...")}
        analyze_client = config.ai_task_settings.get_ai_client("DASHBOARD_ANALYZE")
        analyze_task = config.ai_task_settings.tasks.get("DASHBOARD_ANALYZE")
        if not analyze_client or not analyze_task:
            yield {"data": error("AI task 'DASHBOARD_ANALYZE' is not configured.")}
            return

        analysis_kwargs = {"enable_web_search": False} if plan.disable_web_search else {}
        analyze_result = await ai.execute_task_prompt(
            analyze_client,
            analyze_task,
            dashboard_analysis.build_analysis_prompt(plan),
            **analysis_kwargs,
        )

        if not analyze_result.success:
            yield {"data": error(f"Failed to generate analysis: {analyze_result.error}")}
            return
        if not analyze_result.completion.strip():
            yield {"data": error("The AI returned an empty analysis. Please try again.")}
            return

        # Step 4: Done
        yield {"data": progress(4, total_steps, "Done!")}
        yield {"data": result(analyze_result.completion)}

    return EventSourceResponse(event_generator())
