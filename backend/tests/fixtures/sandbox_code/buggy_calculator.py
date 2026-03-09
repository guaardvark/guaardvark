"""A calculator with an obvious bug for the agent to find and fix."""

def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

def multiply(a, b):
    return a * b

def divide(a, b):
    return a * b  # BUG: should be a / b
