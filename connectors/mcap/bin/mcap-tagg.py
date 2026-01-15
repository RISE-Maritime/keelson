#!/usr/bin/env python3

import os
import logging
import argparse

from keelson_connectors_common import setup_logging

logger = logging.getLogger("mcap-annotation")


def main():

    parser = argparse.ArgumentParser(
        prog="mcap-tagg",
        description="A pure python mcap post processing tool with annotation capabilities",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--log-level",
        type=int,
        default=logging.INFO,
        help="Log level 10=DEBUG, 20=INFO, 30=WARNING",
    )

    parser.add_argument(
        "-id",
        "--input-dir",
        type=str,
        required=True,
        help="The directory containing the files to be processed (files must be in .mcap format)",
    )

    parser.add_argument(
        "-od",
        "--output-dir",
        type=str,
        required=False,
        help="The directory to save the processed files (default is the input directory)",
    )

    # Parse arguments and start doing our thing
    args = parser.parse_args()

    # Setup logger
    setup_logging(level=args.log_level)

    logger.info("Starting the mcap TAGGING...")

    run(args)


def run(args):

    # Get the list of files in the input directory
    files = os.listdir(args.input_dir)

    # Process each file
    for file in files:
        logger.info(f"Processing file: {file}")

        # Check if the file has a .mcap extension
        if not file.endswith(".mcap"):
            logger.warning(f"Skipping non-mcap file: {file}")
            continue

        # Recover the file
        logging.info(f"Recovering file {file}")
        new_file_name = file.split("_")[-1]
        command = f"mcap recover { args.input_dir +'/'+ file} -o {args.input_dir +'/'+ new_file_name}"
        os.system(command)

        # TODO: Output human readable file describing the recorded data
        # - timestamp start and stop
        # - total messages and message keys

        # TODO: Annotate images (object classification)

        # TODO: Download weather data from SMHI

        # TODO: Downlaod AIS data

        logger.info(f"File {file} processed successfully")


if __name__ == "__main__":
    main()
