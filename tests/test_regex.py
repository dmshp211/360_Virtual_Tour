#!/usr/bin/env python3
import sys
sys.path.insert(0, '/Users/a.nazarenko/Downloads/files')
from server import TOUR_ID_RE, valid_tour_id

print('TOUR_ID_RE test:')
print('  valid_tour_id("test-tour"):', valid_tour_id('test-tour'))
print('  valid_tour_id("demo"):', valid_tour_id('demo'))
print('  valid_tour_id("Tour_123"):', valid_tour_id('Tour_123'))
print('  valid_tour_id("тур_demo"):', valid_tour_id('тур_demo'))
print('  valid_tour_id("test/tour"):', valid_tour_id('test/tour'))
