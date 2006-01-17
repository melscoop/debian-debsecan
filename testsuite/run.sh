#!/bin/bash

set -e

export LC_ALL=C

url="file://$(pwd)"

# Check that python-apt is installed.
python -c "import apt_pkg"

for testcase in [0-9][0-9][0-9] ; do
    for format in summary packages bugs detail report ; do
	for suite in sid ; do
	    if test -e $testcase/$suite ; then
		if test -e $testcase/options ; then
		    options="$(cat $testcase/options)"
		else
		    options=""
		fi
		if python ../src/debsecan $options \
		    --suite $suite \
		    --source "$url/$testcase" \
		    --history $testcase/history \
		    --status $testcase/status \
		    --format $format > $testcase/out.$format 2>&1 ; then
		    if test $format = summary ; then
			sort $testcase/out.$format > $testcase/out.$format.1
			mv $testcase/out.$format.1 $testcase/out.$format
		    fi
		    diff -u $testcase/exp.$format $testcase/out.$format
		else
		    echo "FAIL: debsecan failed.  Output follows:"
		    cat $testcase/out.$format
		    exit 1
	        fi
	    fi
	done
    done
done
