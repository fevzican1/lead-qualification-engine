def manifest_gate_active():
    return True

def manifest_gate_ok():
    user = get_current_user()
    return user is not None and user.active
