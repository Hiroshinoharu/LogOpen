# Third-party notices

LogOpen is licensed under GNU GPL version 3 (GPL-3.0-only); see LICENSE.
Third-party software retains its own copyrights and licences.

The runtime dependency versions are recorded in requirements.txt. In particular,
PyQt6 and PyQt6-sip are distributed under their GPLv3 terms in this open-source
build; Qt libraries have their own terms. Python, pywin32, OpenAI's SDK, Pydantic
and other dependencies retain the terms shipped by their respective authors.

Packaged builds include THIRD_PARTY_NOTICES.txt, generated from the installed
distributions' metadata and licence files. Their dist-info metadata is also
bundled. This list does not relicense any dependency.

Corresponding LogOpen source, build scripts and dependency locks are available at
https://github.com/Hiroshinoharu/LogOpen at the release's tagged commit.
Publish the matching source with every binary release. Dependency source links
are included in the generated notices; check the exact bundled Qt/PyQt source
versions and any distribution obligations before release.

The GUI includes Windows font usage through the operating system; Windows font
files are not redistributed by LogOpen. App icons and sound assets in this
repository are part of the LogOpen source distribution.
