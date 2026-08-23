"""Synthetic demo programming exercise for StudyHub Local."""


def classify_variable(example_value):
    if isinstance(example_value, (int, float)):
        return "numerical"
    return "categorical"


if __name__ == "__main__":
    print(classify_variable("tutorial group"))
    print(classify_variable(42))
