from aiogram.fsm.state import State, StatesGroup


class Buy(StatesGroup):
    host = State()
    plan = State()
    confirm = State()


class Extend(StatesGroup):
    confirm = State()


class AdminAddHost(StatesGroup):
    host_name = State()
    host_url = State()
    api_token = State()
    inbound_id = State()
    public_hostname = State()
    public_url = State()
    additional_inbound_ids = State()


class AdminAddPlan(StatesGroup):
    host_name = State()
    plan_name = State()
    months = State()
    price = State()


class AdminBroadcast(StatesGroup):
    message = State()
