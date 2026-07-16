#!/usr/bin/env python3
import sys
sys.path.insert(0, '/Users/a.nazarenko/Downloads/files')
import re

# Test the current TOUR_ID_RE
TOUR_ID_RE_OLD = re.compile(r'^[a-z0-9_-]+$')
TOUR_ID_RE_NEW = re.compile(r'^[a-zA-Z0-9_\-а-яёА-ЯЁ]+$')

print('Testing TOUR_ID_RE:')
print('OLD regex - test-tour:', bool(TOUR_ID_RE_OLD.match('test-tour')))
print('OLD regex - тур_demo:', bool(TOUR_ID_RE_OLD.match('тур_demo')))
print('NEW regex - test-tour:', bool(TOUR_ID_RE_NEW.match('test-tour')))
print('NEW regex - тур_demo:', bool(TOUR_ID_RE_NEW.match('тур_demo')))