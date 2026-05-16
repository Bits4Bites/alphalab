from . import config

# vendor_name and api_tier are initially empty, we want to populate their values from the keys
for vendor_name, api_tiers in list(config.ai_vendor_settings.vendors.items()):
    config.ai_vendor_settings.vendors[vendor_name.upper()] = api_tiers.copy()
    for api_tier, llm_cfg in list(api_tiers.items()):
        # remove any entries that have empty api_key and empty endpoint
        # or empty models list
        if (not llm_cfg.api_key and not llm_cfg.endpoint) or llm_cfg.models is None or not llm_cfg.models:
            del api_tiers[api_tier]
        else:
            config.ai_vendor_settings.vendors[vendor_name.upper()][api_tier.upper()] = llm_cfg.copy()
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
