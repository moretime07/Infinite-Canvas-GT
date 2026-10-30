import unittest

from portable_runtime import listen_options


class PortableListenerTests(unittest.TestCase):
    def test_normal_install_keeps_existing_listener(self):
        self.assertEqual(listen_options({}), ("0.0.0.0", 3000))
        self.assertEqual(listen_options({"INFINITE_CANVAS_PORT": "wrong"}), ("0.0.0.0", 3000))

    def test_portable_is_local_only(self):
        self.assertEqual(listen_options({"INFINITE_CANVAS_PORTABLE": "1"}), ("127.0.0.1", 3000))
        self.assertEqual(listen_options({"INFINITE_CANVAS_PORTABLE": "1", "INFINITE_CANVAS_PORT": "38007"}),
                         ("127.0.0.1", 38007))

    def test_invalid_port_rejected(self):
        for value in ("x", "0", "80", "65536", "-1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                listen_options({"INFINITE_CANVAS_PORTABLE": "1", "INFINITE_CANVAS_PORT": value})


if __name__ == "__main__":
    unittest.main()
