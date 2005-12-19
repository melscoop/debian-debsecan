#!/bin/bash

set -e

export LC_ALL=C

url="file://$(pwd)"

for testcase in [0-9][0-9][0-9] ; do
    for format in summary packages bugs detail report ; do
	for suite in sid ; do
	    if test -e $testcase/$suite ; then
		python ../src/debsecan --suite $suite \
		    --source "$url/$testcase" \
		    --history $testcase/history \
		    --status $testcase/status \
		    --format $format > $testcase/out.$format
		if test $format = summary ; then
		    sort $testcase/out.$format > $testcase/out.$format.1
		    mv $testcase/out.$format.1 $testcase/out.$format
		fi
		diff -u $testcase/exp.$format $testcase/out.$format
	    fi
	done
    done
done
