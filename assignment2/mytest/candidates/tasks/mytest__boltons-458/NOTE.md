test.diff = the upstream test from boltons a1ea103 (tests/test_dictutils.py::test_addlist_iterator)
plus one test added for this task, tests/test_urlutils.py::test_vendored_omd_addlist_iterator:
the upstream fix also changed the vendored OrderedMultiDict in boltons/urlutils.py, but no
upstream test covered that copy. The added test mirrors the upstream one for that class.
