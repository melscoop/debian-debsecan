.PHONY: test

test:
	cd testsuite && sh run.sh

clean:
	-rm -f testsuite/*/*.out testsuite/whitelist.test testsuite/whitelist.exp

quickbuild: test
	dpkg-buildpackage -us -uc --check-command=lintian

release: test clean
	gbp dch --ignore-branch --debian-tag="v%(version)s" --urgency=low --release
	gbp buildpackage --git-tag
