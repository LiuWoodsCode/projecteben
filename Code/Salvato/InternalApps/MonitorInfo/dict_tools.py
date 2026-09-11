def to_dict(obj):
    # Primitive types pass straight through
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj

    # Lists / tuples
    if isinstance(obj, (list, tuple)):
        return [to_dict(item) for item in obj]

    # Dicts
    if isinstance(obj, dict):
        return {key: to_dict(value) for key, value in obj.items()}

    # Objects with attributes
    if hasattr(obj, "__dict__"):
        return {key: to_dict(value) for key, value in vars(obj).items()}

    # Fallback: stringify anything unknown
    return str(obj)