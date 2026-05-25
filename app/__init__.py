import logging

from . import config


class CustomLoggerConfig(logging.Logger):
    def __init__(self, name, level=logging.NOTSET):
        level = logging.WARNING if name.startswith("azure.") else logging.DEBUG if name.startswith("app.") else level
        super().__init__(name, level)


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s: %(message)s")
logging.setLoggerClass(CustomLoggerConfig)

# config.ai_vendor_settings.vendors is in the following format:
# {
#     "vendor_name": {
#         "api_tier": AIVendorConfig(
#             vendor_name="vendor_name",
#             api_tier="api_tier",
#             api_key="api_key",
#             endpoint="endpoint",
#             models={"model1", "model2"}
#         )
#     }
# }
# vendor_name and api_tier are initially empty, we want to populate their values from the keys
for vendor_name, api_tiers in list(config.ai_vendor_settings.vendors.items()):
    config.ai_vendor_settings.vendors[vendor_name.upper()] = api_tiers.copy()
    for api_tier, llm_cfg in list(api_tiers.items()):
        # remove any entries that have empty api_key and empty endpoint
        # or empty models list
        if (not llm_cfg.api_key and not llm_cfg.endpoint) or llm_cfg.models is None or not llm_cfg.models:
            del api_tiers[api_tier]
        else:
            config.ai_vendor_settings.vendors[vendor_name.upper()][api_tier.upper()] = llm_cfg.model_copy()
            config.ai_vendor_settings.vendors[vendor_name.upper()][api_tier.upper()].vendor_name = vendor_name.upper()
            config.ai_vendor_settings.vendors[vendor_name.upper()][api_tier.upper()].api_tier = api_tier.upper()

for vendor_name, api_tiers in list(config.ai_vendor_settings.vendors.items()):
    # final cleanup
    for api_tier, llm_cfg in list(api_tiers.items()):
        if not llm_cfg.vendor_name or not llm_cfg.api_tier:
            del api_tiers[api_tier]

    # delete the whole vendor if it has no api_tiers left
    if not api_tiers:
        del config.ai_vendor_settings.vendors[vendor_name]


# config.ai_task_settings.tasks is in the following format:
# {
#     "task_id": AITaskConfig(
#         task_id="task_id",
#         vendor="vendor_name",
#         tier="api_tier",
#         model="model_name"
#     )
# }
# task_id is initially empty, we want to populate its value from the keys
for task_id, llm_task_cfg in list(config.ai_task_settings.tasks.items()):
    config.ai_task_settings.tasks[task_id.upper()] = llm_task_cfg.model_copy()
    config.ai_task_settings.tasks[task_id.upper()].task_id = task_id.upper()

for task_id, llm_task_cfg in list(config.ai_task_settings.tasks.items()):
    # remove any entries that have empty vendor or empty tier or empty model
    if not llm_task_cfg.task_id or not llm_task_cfg.vendor or not llm_task_cfg.tier or not llm_task_cfg.model:
        del config.ai_task_settings.tasks[task_id]
