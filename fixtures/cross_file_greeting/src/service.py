from src.messages import greeting


def welcome(user):
    return greeting(user["name"])
