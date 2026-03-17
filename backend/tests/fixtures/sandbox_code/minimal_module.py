"""Minimal module for testing feature addition."""


def greet(name):
    return f"Hello, {name}!"


def farewell(name):
    pass  # BUG: should return f"Goodbye, {name}!" but returns nothing
