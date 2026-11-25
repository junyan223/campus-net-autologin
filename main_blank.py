import os
import sys

os.environ["CAMPUSNET_CHANNEL"] = "blank"

from main import main

if __name__ == "__main__":
    sys.exit(main())