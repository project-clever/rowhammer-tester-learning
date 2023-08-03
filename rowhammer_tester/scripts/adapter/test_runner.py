import re

action_pattern = re.compile(r'HAMMER[(]\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*[)]')


class HammerAction:
    def __init__(self, action: str):
        self.bitflips = None
        self.reads = None
        self.row = None
        self.parse_action(action)

    def parse_action(self, action: str):
        parsed_action = re.search(action_pattern, action)

        self.row = parsed_action.group(1)
        self.reads = parsed_action.group(2)
        self.bitflips = parsed_action.group(3)


def run_test(actions, pattern='all_1'):
    rows = []
    flips = {}

    for a_str in actions:
        action = HammerAction(a_str)
        rows.append(action.row)

    for r in rows:
        flips[r] = 0

    return flips


def simplify_test(actions):
    # Put together actions involving same row
    yield

# if __name__ == "__main__":
#     action = HammerAction('HAMMER(1,5,8)')
#     print(action.bitflips)
