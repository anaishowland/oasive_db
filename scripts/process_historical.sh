#!/bin/bash
# Script to monitor unzip and process SFLLD historical data

SFLLD_DIR=~/Downloads/sflld
LOG_FILE=$SFLLD_DIR/processing.log
VENV_PATH=/Users/anaishowland/oasive_db/venv

echo "$(date): Starting historical data processing monitor..." >> $LOG_FILE

# Wait for unzip to complete
while pgrep -x unzip > /dev/null; do
    echo "$(date): Unzip still running..." >> $LOG_FILE
    sleep 30
done

echo "$(date): Unzip completed!" >> $LOG_FILE

# Count extracted files
FILE_COUNT=$(ls $SFLLD_DIR/*.zip 2>/dev/null | wc -l)
echo "$(date): Found $FILE_COUNT year files to process" >> $LOG_FILE

# Activate venv and run ingestor
cd /Users/anaishowland/oasive_db
source $VENV_PATH/bin/activate

echo "$(date): Starting SFLLD ingestor..." >> $LOG_FILE
python3 -m src.ingestors.sflld_ingestor --process $SFLLD_DIR >> $LOG_FILE 2>&1

echo "$(date): Processing complete!" >> $LOG_FILE
