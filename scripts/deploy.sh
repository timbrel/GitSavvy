if [ "$TRAVIS_OS_NAME" -ne "linux" ]; then
    # We'll only want to deploy from 
    exit 0;
fi

PULL_REQUEST_NUMBER=$(git show HEAD --format=format:%s | sed -nE 's/Merge pull request #([0-9]+).*/\1/p')
if [ -z "$PULL_REQUEST_NUMBER" ]; then
    echo "No pull request number found; aborting publish."
else
    SEMVER_CHANGE=$(curl "https://maintainerd.divmain.com/api/semver?repoPath=divmain/GitSavvy&installationId=31333&prNumber=$PULL_REQUEST_NUMBER")
    if [ -z "$SEMVER_CHANGE" ]; then
        echo "No semver selection found; aborting publish."
    else
        MOST_RECENT_TAG=$(git describe --abbrev=0)
        VERSION_ARRAY=( ${MOST_RECENT_TAG//./ } )

        if [ "$SEMVER_CHANGE" == "major" ]; then
            ((VERSION_ARRAY[0]++))
            VERSION_ARRAY[1]=0
            VERSION_ARRAY[2]=0
        elif [ "$SEMVER_CHANGE" == "minor" ]; then
            ((VERSION_ARRAY[1]++))
            VERSION_ARRAY[2]=0
        elif [ "$SEMVER_CHANGE" == "patch" ]; then
            ((VERSION_ARRAY[2]++))
        else
            echo "Matching semantic version not found; aborting publish."
        fi

        git config --global user.name "Dale Bustad (automated)"
        git config --global user.email "dale@divmain.com"

        # Travis is messy and will leave the working directory in an unclean state.
        git reset --hard
        git tag -a "${VERSION_ARRAY[0]}.${VERSION_ARRAY[1]}.${VERSION_ARRAY[2]}" -m "v${VERSION_ARRAY[0]}.${VERSION_ARRAY[1]}.${VERSION_ARRAY[2]}"

        git remote add origin-deploy https://${GH_TOKEN}@github.com/divmain/GitSavvy.git > /dev/null 2>&1
        git push --quiet --tags origin-deploy master
    fi
fi
