from ccgate.transcript import encode_cwd


class TestEncodeCwd:
    def test_windows_drive_colon_becomes_two_hyphens(self):
        result = encode_cwd("C:\\Users\\fred\\project")
        assert result.startswith("c--Users-fred-project")

    def test_windows_dot_in_username(self):
        result = encode_cwd("C:\\Users\\developer\\CCGate")
        assert result == "c--Users-developer-CCGate"

    def test_windows_space_in_path(self):
        result = encode_cwd("C:\\Users\\fred\\OneDrive - Corp\\Docs")
        assert result == "c--Users-fred-OneDrive---Corp-Docs"

    def test_posix_path(self):
        result = encode_cwd("/home/user/projects/ccgate")
        assert result == "-home-user-projects-ccgate"

    def test_known_real_path(self):
        result = encode_cwd("C:\\Users\\developer\\OneDrive - Corp\\Documents\\CCGate")
        assert result == "c--Users-developer-OneDrive---Corp-Documents-CCGate"

    def test_no_trailing_content_lost(self):
        a = encode_cwd("/a/b/c")
        b = encode_cwd("/a/b/cd")
        assert a != b
