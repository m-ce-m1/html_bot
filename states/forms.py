from aiogram.fsm.state import State, StatesGroup


class RegistrationState(StatesGroup):
    waiting_full_name = State()


class TestState(StatesGroup):
    choosing_topic = State()
    answering = State()


class AddTopicState(StatesGroup):
    waiting_title = State()


class UploadTestState(StatesGroup):
    choosing_topic = State()
    awaiting_file = State()


class MaterialState(StatesGroup):
    choosing_topic = State()
    choosing_type = State()
    awaiting_payload = State()


class DeleteMaterialState(StatesGroup):
    choosing_topic = State()
    choosing_material = State()


class QuestionState(StatesGroup):
    awaiting_question = State()
    awaiting_answer = State()


