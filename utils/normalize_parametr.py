def normalize_params(params_dict: dict) -> dict:
    result = {}

    for key, values in params_dict.items():
        if not values:
            continue

        if isinstance(values, dict):
            for sub_key, sub_value in values.items():
                if not sub_value:
                    continue
                result[f'params[{key}][{sub_key}]'] = str(sub_value)

        elif isinstance(values, (list, tuple)):
            for index, value in enumerate(values):
                if not value:
                    continue
                result[f'params[{key}][{index}]'] = str(value)

        else:
            result[f'params[{key}]'] = str(values)

    return result
