from scanborn.__main__ import main


class TestMain:
    def test_main(self, capsys):
        main()
        captured = capsys.readouterr()
        assert captured.out == "ScanBorn\n"
