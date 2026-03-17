"""
Password validation utilities for enforcing strong password requirements.
"""

import re
import string
from typing import Dict, List, Optional, Tuple


class PasswordValidator:
    """
    Password validator with configurable strength requirements.
    """

    def __init__(
        self,
        min_length: int = 12,
        require_uppercase: bool = True,
        require_lowercase: bool = True,
        require_digits: bool = True,
        require_special: bool = True,
        min_special_count: int = 1,
        max_repeating_chars: int = 3,
        forbid_common_passwords: bool = True,
    ):
        """
        Initialize password validator with security requirements.

        Args:
            min_length: Minimum password length
            require_uppercase: Require at least one uppercase letter
            require_lowercase: Require at least one lowercase letter
            require_digits: Require at least one digit
            require_special: Require at least one special character
            min_special_count: Minimum number of special characters
            max_repeating_chars: Maximum consecutive repeating characters
            forbid_common_passwords: Check against common password list
        """
        self.min_length = min_length
        self.require_uppercase = require_uppercase
        self.require_lowercase = require_lowercase
        self.require_digits = require_digits
        self.require_special = require_special
        self.min_special_count = min_special_count
        self.max_repeating_chars = max_repeating_chars
        self.forbid_common_passwords = forbid_common_passwords

        # Common weak passwords to prohibit
        self.common_passwords = {
            "password",
            "password123",
            "12345678",
            "qwerty123",
            "admin123",
            "password1",
            "welcome123",
            "letmein",
            "monkey123",
            "dragon123",
            "princess",
            "password12",
            "qwertyuiop",
            "abc123456",
            "welcome1",
            "admin",
            "administrator",
            "root",
            "user",
            "test",
            "guest",
            "changeme",
            "default",
            "secret",
            "password321",
            "qwerty",
            "123456789",
            "1234567890",
            "abcdefgh",
            "iloveyou",
            "trustno1",
            "shadow",
            "sunshine",
            "master",
            "freedom",
            "whatever",
            "passw0rd",
            "p@ssw0rd",
            "p@ssword",
            "password!",
            "Password1",
            "Password123",
            "P@ssw0rd",
            "P@ssword123",
            "Welcome123",
            "Admin123",
            "Login123",
            "User123",
            "Test123",
            "Guest123",
        }

    def validate_password(
        self, password: str, username: Optional[str] = None
    ) -> Tuple[bool, List[str]]:
        """
        Validate password against all configured requirements.

        Args:
            password: Password to validate
            username: Optional username to check for similarity

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []

        if not password:
            return False, ["Password cannot be empty"]

        # Check minimum length
        if len(password) < self.min_length:
            errors.append(
                f"Password must be at least {self.min_length} characters long"
            )

        # Check character requirements
        if self.require_uppercase and not any(c.isupper() for c in password):
            errors.append("Password must contain at least one uppercase letter")

        if self.require_lowercase and not any(c.islower() for c in password):
            errors.append("Password must contain at least one lowercase letter")

        if self.require_digits and not any(c.isdigit() for c in password):
            errors.append("Password must contain at least one digit")

        if self.require_special:
            special_chars = set(string.punctuation)
            special_count = sum(1 for c in password if c in special_chars)
            if special_count < self.min_special_count:
                errors.append(
                    f"Password must contain at least {self.min_special_count} special character(s)"
                )

        # Check for excessive repeating characters
        if self._has_excessive_repeating_chars(password):
            errors.append(
                f"Password cannot have more than {self.max_repeating_chars} consecutive repeating characters"
            )

        # Check against common passwords
        if self.forbid_common_passwords and password.lower() in self.common_passwords:
            errors.append("Password is too common and easily guessable")

        # Check similarity to username
        if username and self._is_similar_to_username(password, username):
            errors.append("Password cannot be similar to username")

        # Check for keyboard patterns
        if self._contains_keyboard_pattern(password):
            errors.append("Password cannot contain obvious keyboard patterns")

        # Check for sequential patterns
        if self._contains_sequential_pattern(password):
            errors.append(
                "Password cannot contain sequential patterns (e.g., 123456, abcdef)"
            )

        return len(errors) == 0, errors

    def _has_excessive_repeating_chars(self, password: str) -> bool:
        """Check if password has too many consecutive repeating characters."""
        if len(password) <= self.max_repeating_chars:
            return False

        for i in range(len(password) - self.max_repeating_chars):
            if len(set(password[i : i + self.max_repeating_chars + 1])) == 1:
                return True

        return False

    def _is_similar_to_username(self, password: str, username: str) -> bool:
        """Check if password is too similar to username."""
        if not username:
            return False

        password_lower = password.lower()
        username_lower = username.lower()

        # Check if username is contained in password
        if username_lower in password_lower or password_lower in username_lower:
            return True

        # Check if password is just username with numbers/special chars
        password_alphanumeric = "".join(c for c in password_lower if c.isalnum())
        if password_alphanumeric == username_lower:
            return True

        return False

    def _contains_keyboard_pattern(self, password: str) -> bool:
        """Check for common keyboard patterns."""
        keyboard_patterns = [
            "qwerty",
            "qwertyuiop",
            "asdf",
            "asdfgh",
            "asdfghjkl",
            "zxcv",
            "zxcvbn",
            "zxcvbnm",
            "1234567890",
            "!@#$%^&*()",
            "qwertz",
            "azerty",
            "dvorak",
        ]

        password_lower = password.lower()

        # Check for keyboard patterns of length 4 or more
        for pattern in keyboard_patterns:
            for i in range(len(pattern) - 3):
                if pattern[i : i + 4] in password_lower:
                    return True

        return False

    def _contains_sequential_pattern(self, password: str) -> bool:
        """Check for sequential patterns in password."""
        # Check for numeric sequences
        for i in range(len(password) - 3):
            substr = password[i : i + 4]
            if substr.isdigit():
                digits = [int(d) for d in substr]
                if all(digits[j] + 1 == digits[j + 1] for j in range(len(digits) - 1)):
                    return True
                if all(digits[j] - 1 == digits[j + 1] for j in range(len(digits) - 1)):
                    return True

        # Check for alphabetic sequences
        for i in range(len(password) - 3):
            substr = password[i : i + 4].lower()
            if substr.isalpha():
                if all(
                    ord(substr[j]) + 1 == ord(substr[j + 1])
                    for j in range(len(substr) - 1)
                ):
                    return True
                if all(
                    ord(substr[j]) - 1 == ord(substr[j + 1])
                    for j in range(len(substr) - 1)
                ):
                    return True

        return False

    def generate_password_requirements_text(self) -> str:
        """Generate human-readable password requirements text."""
        requirements = [f"At least {self.min_length} characters long"]

        if self.require_uppercase:
            requirements.append("Contains at least one uppercase letter")

        if self.require_lowercase:
            requirements.append("Contains at least one lowercase letter")

        if self.require_digits:
            requirements.append("Contains at least one digit")

        if self.require_special:
            requirements.append(
                f"Contains at least {self.min_special_count} special character(s)"
            )

        requirements.extend(
            [
                f"No more than {self.max_repeating_chars} consecutive repeating characters",
                "Cannot be a common or easily guessable password",
                "Cannot be similar to your username",
                "Cannot contain obvious keyboard or sequential patterns",
            ]
        )

        return "Password must meet the following requirements:\n" + "\n".join(
            requirements
        )


# Default password validator with strong requirements
default_password_validator = PasswordValidator(
    min_length=12,
    require_uppercase=True,
    require_lowercase=True,
    require_digits=True,
    require_special=True,
    min_special_count=2,
    max_repeating_chars=2,
    forbid_common_passwords=True,
)

# More lenient validator for legacy systems
legacy_password_validator = PasswordValidator(
    min_length=8,
    require_uppercase=True,
    require_lowercase=True,
    require_digits=True,
    require_special=False,
    min_special_count=0,
    max_repeating_chars=3,
    forbid_common_passwords=True,
)


def validate_password_strength(
    password: str, username: Optional[str] = None, strict: bool = True
) -> Dict[str, any]:
    """
    Validate password strength using default validators.

    Args:
        password: Password to validate
        username: Optional username for similarity check
        strict: Use strict validation (default) or legacy validation

    Returns:
        Dictionary with validation results
    """
    validator = default_password_validator if strict else legacy_password_validator
    is_valid, errors = validator.validate_password(password, username)

    return {
        "is_valid": is_valid,
        "errors": errors,
        "requirements": validator.generate_password_requirements_text(),
        "strength_level": _calculate_strength_level(password),
    }


def _calculate_strength_level(password: str) -> str:
    """Calculate password strength level."""
    if not password:
        return "very_weak"

    score = 0

    # Length scoring
    if len(password) >= 8:
        score += 1
    if len(password) >= 12:
        score += 1
    if len(password) >= 16:
        score += 1

    # Character diversity
    if any(c.islower() for c in password):
        score += 1
    if any(c.isupper() for c in password):
        score += 1
    if any(c.isdigit() for c in password):
        score += 1
    if any(c in string.punctuation for c in password):
        score += 1

    # Complexity bonus
    if len(set(password)) >= len(password) * 0.7:  # Good character diversity
        score += 1

    if score <= 2:
        return "very_weak"
    elif score <= 4:
        return "weak"
    elif score <= 6:
        return "medium"
    elif score <= 7:
        return "strong"
    else:
        return "very_strong"
