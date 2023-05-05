#!/usr/bin/env python3

import argparse
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import docker
import shutil
import traceback
import concurrent.futures

# Function to get installed packages from a container image
def get_installed_packages(image):
    with tempfile.TemporaryDirectory() as tmpdir:
        container = client.containers.create(image.id)
        script_path = Path(tmpdir) / 'list_installed_packages.sh'

        with script_path.open('w') as script_file:
            script_file.write('''#!/bin/sh
            if command -v apt-get >/dev/null 2>&1; then
                apt list --installed
            elif command -v yum >/dev/null 2>&1; then
                yum list installed
            fi
            ''')

        os.chmod(script_path, 0o755)

        tar_stream = io.BytesIO()
        with tar_stream:
            with tempfile.TemporaryDirectory() as tmp_tar_dir:
                shutil.copy(script_path, tmp_tar_dir)
                shutil.make_archive(os.path.join(tmp_tar_dir, 'archive'), 'gztar', tmp_tar_dir)
                with open(os.path.join(tmp_tar_dir, 'archive.tar.gz'), 'rb') as tar_file:
                    tar_stream.write(tar_file.read())
            tar_stream.seek(0)
            container.put_archive('/', tar_stream.getvalue())

        try:
            container.start()
            exit_code, output = container.exec_run('/list_installed_packages.sh')
            container.stop()
            container.remove()
        except Exception as e:
            print("Exception:", e)
            traceback.print_exc()
            print("Failed to list installed packages in the old image:")
            sys.exit(1)

        installed_packages = []

        if 'apt' in output.decode('utf-8'):
            installed_packages = [line.split('/')[0] for line in output.decode('utf-8').splitlines() if '/' in line]
        elif 'yum' in output.decode('utf-8'):
            installed_packages = [line.split()[0] for line in output.decode('utf-8').splitlines() if '.' in line]

        return installed_packages

# Function to generate a Dockerfile with the new base image and required components
def generate_dockerfile(base_image, packages):
    dockerfile = f'''
    FROM {base_image}

    RUN apt-get update && \\
        apt-get install -y \\
        {' '.join(packages)} && \\
        rm -rf /var/lib/apt/lists/*

    # Configure the application environment (set environment variables, working directory, user permissions, etc.)
    # ...

    # Define the default command to run when the container is started
    # ENTRYPOINT or CMD instruction (e.g., CMD ["python", "app.py"])
    '''

    return dockerfile

# Function to build a new container image using the provided Dockerfile
def build_container_image(dockerfile, tag):
    with tempfile.TemporaryDirectory() as tmpdir:
        dockerfile_path = Path(tmpdir) / 'Dockerfile'
        with dockerfile_path.open('w') as dockerfile_file:
            dockerfile_file.write(dockerfile)

        print(f"Building new container image: {tag}")
        try:
            new_image, build_logs = client.images.build(path=str(tmpdir), tag=tag, rm=True, pull=True)
            for log in build_logs:
                print(log.get('stream', '').strip())
        except docker.errors.BuildError as e:
            print(f"Error building the new image: {e}")
            for log in e.build_log:
                print(log.get('stream', '').strip())
            sys.exit(1)
        except Exception as e:
            print(f"An unexpected error occurred while building the new image: {e}")
            sys.exit(1)

    return new_image

# Set up argument parser
parser = argparse.ArgumentParser(description='Build a new container image using a different base image.')
parser.add_argument('--old-image', required=True, help='Existing container image to analyze')
parser.add_argument('--new-base-image', required=True, help='New base image to use')
parser.add_argument('--new-image', required=True, help='Name for the new container image')

args = parser.parse_args()

# Initialize Docker client
client = docker.from_env()

# Analyze the existing container image
old_image = client.images.get(args.old_image)

# Get installed packages from the old image
installed_packages = get_installed_packages(old_image)

# Generate a Dockerfile with the new base image and required components
dockerfile = generate_dockerfile(args.new_base_image, installed_packages)

# Build and test the new container image
new_image = build_container_image(dockerfile, args.new_image)

print(f"New container image '{args.new_image}' has been built successfully.")
