"""Poorly structured code for the agent to improve."""


def processData(x):
    if x == None:
        return None
    if type(x) == str:
        return x.strip().lower()
    if type(x) == int or type(x) == float:
        return x * 2
    return x
