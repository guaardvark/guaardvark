#!/bin/bash
VADER_RED="\033[38;5;196m"
VADER_RED_DARK="\033[38;5;88m"
VADER_RED_LIGHT="\033[38;5;203m"
VADER_GRAY="\033[38;5;240m"
VADER_GRAY_DARK="\033[38;5;236m"
VADER_WHITE="\033[38;5;255m"
VADER_WHITE_DIM="\033[38;5;248m"
VADER_RESET="\033[0m"
VADER_BOLD="\033[1m"

vader_header() { echo -e "${VADER_RED}${VADER_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${VADER_RESET}"; }
vader_separator() { echo -e "${VADER_GRAY_DARK}─────────────────────────────────────────────────────────────────${VADER_RESET}"; }
vader_title() { echo -e "${VADER_WHITE}${VADER_BOLD}$1${VADER_RESET}"; }
vader_info() { echo -e "  ${VADER_GRAY}·${VADER_RESET} ${VADER_WHITE_DIM}$1${VADER_RESET}"; }
vader_success() { echo -e "  ${VADER_RED}✔${VADER_RESET} ${VADER_WHITE}$1${VADER_RESET}"; }
vader_warn() { echo -e "  ${VADER_RED_LIGHT}⚠${VADER_RESET} ${VADER_RED_LIGHT}$1${VADER_RESET}"; }
vader_error() { echo -e "  ${VADER_RED_DARK}✖${VADER_RESET} ${VADER_RED}$1${VADER_RESET}"; }
vader_step() { echo -e "${VADER_RED}${VADER_BOLD}► [$1/10]${VADER_RESET} ${VADER_WHITE}${VADER_BOLD}$2${VADER_RESET}"; }

vader_header
vader_title "  Guaardvark Startup Script v5.1 - Smart Install Mode"
vader_header
vader_step 1 "Checking environment dependencies..."
vader_info "Creating Python venv at \$GUAARDVARK_ROOT/backend/venv..."
vader_success "Backend is healthy"
vader_warn "Frontend build is stale (src newer than dist)"
vader_error "Migration failed."
vader_separator
