import os
import sys
sys.path.insert(0, os.path.abspath("."))

from services.search_provisioner import provision_search_resources
provision_search_resources()
