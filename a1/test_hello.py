import unittest

from hello import hello


class TestHello(unittest.TestCase):
    def test_default(self):
        self.assertEqual(hello(), "Hello, World!")

    def test_with_name(self):
        self.assertEqual(hello("Alice"), "Hello, Alice!")


if __name__ == "__main__":
    unittest.main()
