CC=clang
OUTPUT=com.dortania.opencore-legacy-patcher.privileged-helper

all: clean release

# The helper is retired: it only refuses requests and links no frameworks.
release: main.m
	$(CC) -Wall -arch x86_64 -arch arm64 -mmacosx-version-min=10.9 -o $(OUTPUT) main.m

debug: release

clean:
	/bin/rm -f $(OUTPUT)
