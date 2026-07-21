@echo off
cd /d E:\Trade
set PYTHONUTF8=1
set TF_CPP_MIN_LOG_LEVEL=3
set CUDA_VISIBLE_DEVICES=

echo ============================================
echo VN Stock Dashboard Test Suite
echo ============================================

echo.
echo [1/3] Unit + Integration Tests...
python -m pytest tests/test_dashboard.py::TestDataFiles tests/test_dashboard.py::TestCoreLogic tests/test_dashboard.py::TestRegression -v

echo.
echo [2/3] Performance Tests...
python -m pytest tests/test_dashboard.py::TestPerformance -v

echo.
echo [3/3] Visual Tests (requires dashboard running)...
python -m pytest tests/test_dashboard.py::TestVisual -v --headed

echo.
echo ============================================
echo Done!
pause
